"""SQL Server catalog introspection through ``sys.*``.

This is the source-only path: read a SQL Server schema, build the canonical
catalog, and let the application verify it. Generation and dump loading come
later.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Self

from ...core.ir import (
    Catalog,
    CheckConstraint,
    Column,
    DataKind,
    ForeignKey,
    Function,
    Partition,
    Provenance,
    RuleCandidate,
    SqlObject,
    Table,
    TemporalInfo,
    Trigger,
    UniqueConstraint,
)
from .dialect import SqlServerDialect, strip_check_definition, type_text

logger = logging.getLogger(__name__)

SYSTEM_SCHEMAS = {"sys", "INFORMATION_SCHEMA", "guest"}


class SqlServerConnection:
    """Thin wrapper that guarantees context-manager and close semantics."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self._closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        self.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.connection.close()



@contextmanager
def _cursor(conn: Any) -> Iterator[Any]:
    cursor = conn.cursor()
    try:
        yield cursor
    finally:
        cursor.close()


def connect(dsn: str) -> SqlServerConnection:
    try:
        import pyodbc
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise RuntimeError(
            "SQL Server support needs pyodbc; install smoker[sqlserver]"
        ) from exc
    return SqlServerConnection(pyodbc.connect(dsn))


def _type_name(row: Any) -> str:
    return str(row.type_name)


def _column_row(
    table: Table, row: Any, dialect: SqlServerDialect
) -> Column:
    type_name = _type_name(row)
    sql_type = type_text(
        type_name,
        int(row.max_length) if row.max_length is not None else None,
        int(row.precision) if row.precision is not None else None,
        int(row.scale) if row.scale is not None else None,
    )
    default = row.default_definition.strip() if row.default_definition else None
    if default and default.startswith("(") and default.endswith(")"):
        default = strip_check_definition(default)
    generated = None
    if row.is_computed:
        generated = "computed"
    elif int(row.generated_always_type or 0) != 0:
        generated = "system"
    elif type_name.lower() in {"timestamp", "rowversion"}:
        generated = "rowversion"
    return Column(
        name=str(row.column_name),
        sql_type=sql_type,
        nullable=bool(row.is_nullable),
        default=default,
        identity="a" if row.is_identity else None,
        generated=generated,
        data_kind=dialect.classify_type(sql_type),
        comment=None,
    )


def _tables(
    conn: Any, dialect: SqlServerDialect, database: str | None = None
) -> dict[str, Table]:
    tables: dict[str, Table] = {}
    with _cursor(conn) as cursor:
        cursor.execute(
            """
            SELECT s.name AS schema_name, t.name AS table_name, t.object_id,
                   t.temporal_type,
                   c.column_id, c.name AS column_name,
                   TYPE_NAME(c.system_type_id) AS type_name,
                   c.max_length, c.precision, c.scale, c.is_nullable,
                   c.is_identity, c.is_computed, c.generated_always_type,
                   dc.definition AS default_definition,
                   cc.definition AS computed_definition
            FROM sys.tables t
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            JOIN sys.columns c ON c.object_id = t.object_id
            LEFT JOIN sys.default_constraints dc
              ON dc.parent_object_id = t.object_id
             AND dc.parent_column_id = c.column_id
            LEFT JOIN sys.computed_columns cc
              ON cc.object_id = t.object_id
             AND cc.column_id = c.column_id
            WHERE t.is_ms_shipped = 0
              AND s.name NOT IN ('sys', 'INFORMATION_SCHEMA', 'guest')
              AND t.temporal_type <> 1
            ORDER BY s.name, t.name, c.column_id
            """
        )
        rows = cursor.fetchall()

    by_object: dict[int, Table] = {}
    for row in rows:
        key = f"{row.schema_name}.{row.table_name}"
        table = by_object.get(int(row.object_id))
        if table is None:
            table = Table(
                schema=str(row.schema_name),
                name=str(row.table_name),
                provenance=Provenance("catalog", "sys.tables", "table"),
                database=database,
            )
            by_object[int(row.object_id)] = table
            tables[key] = table
        column = _column_row(table, row, dialect)
        if row.computed_definition:
            column.default = str(row.computed_definition)
        table.columns.append(column)

    try:
        with _cursor(conn) as cursor:
            cursor.execute(
                """
                SELECT p.object_id, SUM(p.rows) AS row_count
                FROM sys.dm_db_partition_stats p
                WHERE p.index_id IN (0, 1)
                GROUP BY p.object_id
                """
            )
            for object_id, row_count in cursor.fetchall():
                table = by_object.get(int(object_id))
                if table is not None:
                    table.estimated_rows = int(row_count or 0)
    except Exception as exc:  # noqa: BLE001 - estimates are optional
        logger.debug("could not read row estimates: %s", exc)
    return tables


def _constraints(conn: Any, tables: dict[str, Table]) -> None:
    by_object = {(table.schema, table.name): table for table in tables.values()}
    object_ids: dict[int, Table] = {}
    # Rebuild the object_id -> table map from the catalog order.
    with _cursor(conn) as cursor:
        cursor.execute(
            "SELECT object_id, schema_name, table_name FROM ("
            "SELECT t.object_id, s.name AS schema_name, t.name AS table_name "
            "FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id"
            ") q"
        )
        for object_id, schema_name, table_name in cursor.fetchall():
            table = by_object.get((str(schema_name), str(table_name)))
            if table is not None:
                object_ids[int(object_id)] = table

    # Primary keys and unique indexes.
    with _cursor(conn) as cursor:
        cursor.execute(
            """
            SELECT i.object_id, i.index_id, i.name, i.is_primary_key,
                   i.is_unique, i.has_filter, i.filter_definition,
                   ic.key_ordinal, c.name AS column_name
            FROM sys.indexes i
            JOIN sys.index_columns ic
              ON ic.object_id = i.object_id AND ic.index_id = i.index_id
            JOIN sys.columns c
              ON c.object_id = ic.object_id AND c.column_id = ic.column_id
            WHERE i.is_hypothetical = 0
              AND i.is_disabled = 0
              AND ic.key_ordinal > 0
            ORDER BY i.object_id, i.index_id, ic.key_ordinal
            """
        )
        indexes: dict[tuple[int, int], list[str]] = defaultdict(list)
        metadata: dict[tuple[int, int], tuple[str, bool, bool, bool, Any]] = {}
        for row in cursor.fetchall():
            key = (int(row.object_id), int(row.index_id))
            indexes[key].append(str(row.column_name))
            metadata[key] = (
                str(row.name),
                bool(row.is_primary_key),
                bool(row.is_unique),
                bool(row.has_filter),
                row.filter_definition,
            )
    for key, columns in indexes.items():
        object_id, _index_id = key
        name, is_primary, is_unique, has_filter, filter_definition = metadata[key]
        table = object_ids.get(object_id)
        if table is None:
            continue
        if is_primary:
            table.primary_key = columns
            for column_name in columns:
                try:
                    table.column(column_name).nullable = False
                except KeyError:
                    pass
        elif is_unique:
            predicate = None
            if has_filter and filter_definition:
                predicate = strip_check_definition(str(filter_definition))
            table.uniques.append(
                UniqueConstraint(
                    name=name,
                    columns=columns,
                    predicate=predicate,
                    provenance=Provenance("catalog", "sys.indexes", "unique"),
                )
            )

    # Foreign keys.
    with _cursor(conn) as cursor:
        cursor.execute(
            """
            SELECT fk.parent_object_id, fk.name,
                   fk.delete_referential_action_desc,
                   fk.update_referential_action_desc,
                   rs.name AS parent_schema, rt.name AS parent_table,
                   pc.name AS child_column, rc.name AS parent_column,
                   fkc.constraint_column_id
            FROM sys.foreign_keys fk
            JOIN sys.foreign_key_columns fkc
              ON fkc.constraint_object_id = fk.object_id
            JOIN sys.tables pt ON pt.object_id = fk.parent_object_id
            JOIN sys.schemas ps ON ps.schema_id = pt.schema_id
            JOIN sys.tables rt ON rt.object_id = fk.referenced_object_id
            JOIN sys.schemas rs ON rs.schema_id = rt.schema_id
            JOIN sys.columns pc
              ON pc.object_id = fkc.parent_object_id
             AND pc.column_id = fkc.parent_column_id
            JOIN sys.columns rc
              ON rc.object_id = fkc.referenced_object_id
             AND rc.column_id = fkc.referenced_column_id
            WHERE fk.is_ms_shipped = 0
            ORDER BY fk.parent_object_id, fk.name, fkc.constraint_column_id
            """
        )
        groups: dict[tuple[int, str], list[Any]] = defaultdict(list)
        for row in cursor.fetchall():
            groups[(int(row.parent_object_id), str(row.name))].append(row)
    for (object_id, name), rows in groups.items():
        table = object_ids.get(object_id)
        if table is None:
            continue
        first = rows[0]
        table.foreign_keys.append(
            ForeignKey(
                name=name,
                child_schema=table.schema,
                child_table=table.name,
                child_columns=[str(row.child_column) for row in rows],
                parent_schema=str(first.parent_schema),
                parent_table=str(first.parent_table),
                parent_columns=[str(row.parent_column) for row in rows],
                on_delete=str(first.delete_referential_action_desc).lower(),
                provenance=Provenance("catalog", "sys.foreign_keys", "foreign_key"),
            )
        )

    # Check constraints.
    with _cursor(conn) as cursor:
        cursor.execute(
            """
            SELECT cc.parent_object_id, cc.name, cc.definition,
                   cc.is_disabled, cc.is_not_trusted
            FROM sys.check_constraints cc
            WHERE cc.is_ms_shipped = 0
            """
        )
        checks = cursor.fetchall()
    for row in checks:
        table = object_ids.get(int(row.parent_object_id))
        if table is None:
            continue
        predicate = strip_check_definition(str(row.definition))
        table.checks.append(
            CheckConstraint(
                name=str(row.name),
                definition=f"CHECK ({predicate})",
                predicate=predicate,
                validated=not bool(row.is_disabled) and not bool(row.is_not_trusted),
                provenance=Provenance("catalog", "sys.check_constraints", "check"),
            )
        )


def _partitions(conn: Any, tables: dict[str, Table]) -> None:
    by_object: dict[int, Table] = {}
    by_name = {(table.schema, table.name): table for table in tables.values()}
    with _cursor(conn) as cursor:
        cursor.execute(
            "SELECT object_id, schema_name, table_name FROM ("
            "SELECT t.object_id, s.name AS schema_name, t.name AS table_name "
            "FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id"
            ") q"
        )
        for object_id, schema_name, table_name in cursor.fetchall():
            table = by_name.get((str(schema_name), str(table_name)))
            if table is not None:
                by_object[int(object_id)] = table

    query = """
        SELECT t.object_id, c.name AS partition_column,
               pf.boundary_value_on_right,
               prv.boundary_id,
               CAST(prv.value AS nvarchar(100)) AS boundary
        FROM sys.tables t
        JOIN sys.indexes i
          ON i.object_id = t.object_id AND i.index_id IN (0, 1)
        JOIN sys.partition_schemes ps
          ON ps.data_space_id = i.data_space_id
        JOIN sys.partition_functions pf
          ON pf.function_id = ps.function_id
        JOIN sys.index_columns ic
          ON ic.object_id = i.object_id
         AND ic.index_id = i.index_id
         AND ic.partition_ordinal > 0
        JOIN sys.columns c
          ON c.object_id = ic.object_id AND c.column_id = ic.column_id
        LEFT JOIN sys.partition_range_values prv
          ON prv.function_id = pf.function_id
        WHERE t.is_ms_shipped = 0
        ORDER BY t.object_id, prv.boundary_id
    """
    try:
        with _cursor(conn) as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
    except Exception as exc:  # noqa: BLE001 - partitioning is optional
        logger.debug("could not read partitions: %s", exc)
        return

    grouped: dict[int, list[Any]] = defaultdict(list)
    column_by_table: dict[int, str] = {}
    for row in rows:
        object_id = int(row.object_id)
        column_by_table[object_id] = str(row.partition_column)
        if row.boundary is not None:
            grouped[object_id].append(row)

    for object_id, boundaries in grouped.items():
        table = by_object.get(object_id)
        if table is None:
            continue
        column_name = column_by_table[object_id]
        try:
            column = table.column(column_name)
        except KeyError:
            continue
        values = []
        for row in boundaries:
            try:
                values.append(_partition_value(row.boundary, column))
            except (TypeError, ValueError):
                continue
        if not values:
            continue
        table.is_partitioned = True
        table.partition_strategy = "range"
        table.partition_key = [column_name]
        table.partitions = [
            Partition(
                schema=table.schema,
                name=f"{table.name}_partition",
                strategy="range",
                columns=[column_name],
                lower=[min(values)],
                upper=[max(values)],
            )
        ]


def _partition_value(value: Any, column: Any) -> Any:
    text = str(value)
    if column.data_kind in {DataKind.SMALLINT, DataKind.INT, DataKind.BIGINT}:
        return int(text)
    if column.data_kind == DataKind.NUMERIC:
        from decimal import Decimal

        return Decimal(text)
    if column.data_kind == DataKind.DATE:
        from datetime import date

        return date.fromisoformat(text.split(" ")[0])
    if column.data_kind in {DataKind.TIMESTAMP, DataKind.TIMESTAMPTZ}:
        from datetime import datetime

        return datetime.fromisoformat(text.replace(" ", "T"))
    return text


def _temporal(
    conn: Any, tables: dict[str, Table], database: str | None = None
) -> None:
    by_object = {(table.schema, table.name): table for table in tables.values()}
    with _cursor(conn) as cursor:
        cursor.execute(
            "SELECT object_id, schema_name, table_name FROM ("
            "SELECT t.object_id, s.name AS schema_name, t.name AS table_name "
            "FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id"
            ") q"
        )
        by_id = {
            int(object_id): by_object.get((str(schema), str(name)))
            for object_id, schema, name in cursor.fetchall()
        }
        cursor.execute(
            """
            SELECT t.object_id, hs.name AS history_schema, ht.name AS history_table
            FROM sys.tables t
            JOIN sys.tables ht ON ht.object_id = t.history_table_id
            JOIN sys.schemas hs ON hs.schema_id = ht.schema_id
            WHERE t.temporal_type = 2
            """
        )
        history = {
            int(object_id): (str(schema), str(name))
            for object_id, schema, name in cursor.fetchall()
        }
        cursor.execute(
            """
            SELECT p.object_id, sc.name AS start_column, ec.name AS end_column
            FROM sys.periods p
            JOIN sys.columns sc
              ON sc.object_id = p.object_id AND sc.column_id = p.start_column_id
            JOIN sys.columns ec
              ON ec.object_id = p.object_id AND ec.column_id = p.end_column_id
            """
        )
        periods = {
            int(object_id): (str(start), str(end))
            for object_id, start, end in cursor.fetchall()
        }
    for object_id, table in by_id.items():
        if table is None or object_id not in history:
            continue
        history_schema, history_name = history[object_id]
        start, end = periods.get(object_id, (None, None))
        table.temporal = TemporalInfo(
            history_table=(
                f"{database}.{history_schema}.{history_name}"
                if database
                else f"{history_schema}.{history_name}"
            ),
            start_column=start,
            end_column=end,
        )


def _triggers(conn: Any, tables: dict[str, Table]) -> None:
    by_object = {f"{table.schema}.{table.name}": table for table in tables.values()}
    with _cursor(conn) as cursor:
        cursor.execute(
            """
            SELECT t.object_id, t.parent_id, t.name, t.is_instead_of_trigger,
                   m.definition, te.type_desc
            FROM sys.triggers t
            JOIN sys.tables parent ON parent.object_id = t.parent_id
            JOIN sys.schemas s ON s.schema_id = parent.schema_id
            LEFT JOIN sys.sql_modules m ON m.object_id = t.object_id
            LEFT JOIN sys.trigger_events te ON te.object_id = t.object_id
            WHERE t.is_ms_shipped = 0
            ORDER BY t.object_id, te.type_desc
            """
        )
        rows = cursor.fetchall()
    with _cursor(conn) as cursor:
        cursor.execute(
            """
            SELECT t.object_id, s.name AS schema_name, parent.name AS table_name
            FROM sys.triggers t
            JOIN sys.tables parent ON parent.object_id = t.parent_id
            JOIN sys.schemas s ON s.schema_id = parent.schema_id
            WHERE t.is_ms_shipped = 0
            """
        )
        parents = {
            int(object_id): f"{schema_name}.{table_name}"
            for object_id, schema_name, table_name in cursor.fetchall()
        }
    groups: dict[int, list[Any]] = defaultdict(list)
    for row in rows:
        groups[int(row.object_id)].append(row)
    for object_id, group in groups.items():
        table = by_object.get(parents.get(object_id, ""))
        if table is None:
            continue
        first = group[0]
        events = ", ".join(
            str(row.type_desc).replace("_", " ")
            for row in group
            if row.type_desc
        )
        definition = str(first.definition or "")
        table.triggers.append(
            Trigger(
                name=str(first.name),
                timing="INSTEAD OF" if first.is_instead_of_trigger else "AFTER",
                events=events or "UNKNOWN",
                function=str(first.name),
                definition=definition,
                body=definition,
                provenance=Provenance("catalog", "sys.triggers", "trigger"),
            )
        )


def _objects(
    conn: Any, tables: dict[str, Table], database: str | None = None
) -> tuple[list[Function], list[SqlObject]]:
    functions: list[Function] = []
    objects: list[SqlObject] = []
    with _cursor(conn) as cursor:
        cursor.execute(
            """
            SELECT s.name AS schema_name, o.name, o.type, o.object_id,
                   m.definition
            FROM sys.objects o
            JOIN sys.schemas s ON s.schema_id = o.schema_id
            LEFT JOIN sys.sql_modules m ON m.object_id = o.object_id
            WHERE o.is_ms_shipped = 0
              AND o.type IN ('FN', 'IF', 'TF', 'P', 'PC')
            ORDER BY s.name, o.name
            """
        )
        for row in cursor.fetchall():
            definition = str(row.definition or "")
            functions.append(
                Function(
                    oid=int(row.object_id),
                    schema=str(row.schema_name),
                    name=str(row.name),
                    body=definition,
                    kind="procedure" if str(row.type).strip() in {"P", "PC"} else "function",
                    language="tsql",
                    provenance=Provenance("catalog", "sys.objects", "function"),
                    database=database,
                )
            )

        cursor.execute(
            """
            SELECT s.name AS schema_name, v.name, m.definition
            FROM sys.views v
            JOIN sys.schemas s ON s.schema_id = v.schema_id
            LEFT JOIN sys.sql_modules m ON m.object_id = v.object_id
            WHERE v.is_ms_shipped = 0
            ORDER BY s.name, v.name
            """
        )
        for row in cursor.fetchall():
            objects.append(
                SqlObject(
                    kind="view",
                    schema=str(row.schema_name),
                    name=str(row.name),
                    definition=str(row.definition or ""),
                    provenance=Provenance("catalog", "sys.views", "view"),
                    database=database,
                )
            )

        cursor.execute(
            """
            SELECT s.name AS schema_name, q.name,
                   CAST(q.start_value AS nvarchar(100)) AS start_value,
                   CAST(q.increment AS nvarchar(100)) AS increment,
                   q.is_cycling
            FROM sys.sequences q
            JOIN sys.schemas s ON s.schema_id = q.schema_id
            ORDER BY s.name, q.name
            """
        )
        for row in cursor.fetchall():
            objects.append(
                SqlObject(
                    kind="sequence",
                    schema=str(row.schema_name),
                    name=str(row.name),
                    definition=(
                        f"CREATE SEQUENCE {row.schema_name}.{row.name} "
                        f"START WITH {row.start_value} INCREMENT BY {row.increment}"
                    ),
                    provenance=Provenance("catalog", "sys.sequences", "sequence"),
                    database=database,
                )
            )

        cursor.execute(
            """
            SELECT ps.name AS scheme_name, pf.name AS function_name,
                   pf.type_desc
            FROM sys.partition_schemes ps
            JOIN sys.partition_functions pf
              ON pf.function_id = ps.function_id
            ORDER BY ps.name
            """
        )
        for row in cursor.fetchall():
            objects.append(
                SqlObject(
                    kind="partition_scheme",
                    schema=None,
                    name=str(row.scheme_name),
                    definition=(
                        f"PARTITION SCHEME {row.scheme_name} AS PARTITION "
                        f"{row.function_name} ({row.type_desc})"
                    ),
                    provenance=Provenance(
                        "catalog", "sys.partition_schemes", "partition_scheme"
                    ),
                    database=database,
                )
            )
    return functions, objects


def _compatible(left: Column, right: Column) -> bool:
    if left.data_kind == right.data_kind:
        return True
    integers = {DataKind.SMALLINT, DataKind.INT, DataKind.BIGINT}
    if left.data_kind in integers and right.data_kind in integers:
        return True
    text = {DataKind.TEXT, DataKind.ENUM}
    return bool(left.data_kind in text and right.data_kind in text)


def _infer_relationships(tables: dict[str, Table]) -> None:
    for table in tables.values():
        for column in table.columns:
            if not column.name.endswith("_id"):
                continue
            if any(column.name in fk.child_columns for fk in table.foreign_keys):
                continue
            base = column.name[: -len("_id")]
            candidates = {base, f"{base}s", f"{base}es"}
            if base.endswith("y"):
                candidates.add(f"{base[:-1]}ies")
            ordered = sorted(
                tables.values(),
                key=lambda item: (item.database != table.database, item.key),
            )
            for candidate in ordered:
                if candidate.name not in candidates or len(candidate.primary_key) != 1:
                    continue
                parent_column = candidate.primary_key[0]
                try:
                    parent = candidate.column(parent_column)
                except KeyError:
                    continue
                if not _compatible(column, parent):
                    continue
                table.foreign_keys.append(
                    ForeignKey(
                        name=f"{table.name}_{column.name}_fkey",
                        child_schema=table.schema,
                        child_table=table.name,
                        child_columns=[column.name],
                        parent_schema=candidate.schema,
                        parent_table=candidate.name,
                        parent_columns=[parent_column],
                        inferred=True,
                        provenance=Provenance(
                            "inferred", "naming", f"{table.key}.{column.name}"
                        ),
                        child_database=table.database,
                        parent_database=candidate.database,
                    )
                )
                break


def _negate_tsql(condition: str) -> str | None:
    text = re.sub(r"^\(+|\)+$", "", condition.strip()).strip()
    if re.search(r"[()]|\b(AND|OR|EXISTS|SELECT|INSERTED|DELETED)\b", text, re.IGNORECASE):
        return None
    flip = {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "=": "<>", "<>": "=", "!=": "="}
    match = re.match(r"^(.+?)\s*(<=|>=|<>|!=|=|<|>)\s*(.+)$", text, re.DOTALL)
    if match and match.group(2) in flip:
        return f"{match.group(1).strip()} {flip[match.group(2)]} {match.group(3).strip()}"
    return None


def _conditions(body: str) -> list[str]:
    return [
        " ".join(match.group(1).split())
        for match in re.finditer(
            r"\bIF\s+(.+?)\s+(?:BEGIN|THROW|RAISERROR)\b",
            body,
            re.IGNORECASE | re.DOTALL,
        )
    ]


def _analyze_functions(functions: list[Function]) -> list[RuleCandidate]:
    rules: list[RuleCandidate] = []
    for function in functions:
        body = function.body or ""
        function.conditions = _conditions(body)
        function.statements = [
            match.group(1).replace("''", "'")
            for match in re.finditer(
                r"\b(?:EXEC(?:UTE)?|sp_executesql)\s*(?:\(\s*)?N?'((?:[^']|'')*)'",
                body,
                re.IGNORECASE | re.DOTALL,
            )
        ]
        function.writes = sorted(
            {
                str(match.group(2))
                for match in re.finditer(
                    r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([\[\]\w.]+)",
                    body,
                    re.IGNORECASE,
                )
            }
        )
        function.reads = sorted(
            {
                str(match.group(1))
                for match in re.finditer(
                    r"\bFROM\s+([\[\]\w.]+)", body, re.IGNORECASE
                )
            }
        )
        if "THROW" not in body.upper() and "RAISERROR" not in body.upper():
            continue
        for condition in function.conditions:
            expression = _negate_tsql(condition)
            if expression is None:
                continue
            rules.append(
                RuleCandidate(
                    id=f"rule:{function.schema}.{function.name}:{len(rules) + 1}",
                    kind="guard",
                    table="",
                    expression=expression,
                    confidence=0.6,
                    applied=False,
                    provenance=Provenance(
                        "function",
                        function.name,
                        f"{function.schema}.{function.name}",
                    ),
                    note=f"IF {condition} THEN THROW/RAISERROR (segmentation only)",
                )
            )
    return rules


def _analyze_triggers(tables: dict[str, Table]) -> list[RuleCandidate]:
    rules: list[RuleCandidate] = []
    for table in tables.values():
        for trigger in table.triggers:
            body = trigger.body or ""
            if "THROW" not in body.upper() and "RAISERROR" not in body.upper():
                continue
            for condition in _conditions(body):
                expression = _negate_tsql(condition)
                if expression is None:
                    continue
                rules.append(
                    RuleCandidate(
                        id=f"rule:{table.key}:{trigger.name}:{len(rules) + 1}",
                        kind="guard",
                        table=table.key,
                        expression=expression,
                        confidence=0.6,
                        applied=False,
                        provenance=Provenance(
                            "trigger", trigger.name, table.key
                        ),
                        note=f"IF {condition} THEN THROW/RAISERROR (segmentation only)",
                    )
                )
    return rules


def _user_databases(conn: Any) -> list[str]:
    with _cursor(conn) as cursor:
        cursor.execute(
            "SELECT name FROM sys.databases "
            "WHERE database_id > 4 AND state = 0 ORDER BY name"
        )
        return [str(row[0]) for row in cursor.fetchall()]


def introspect(
    conn: Any,
    databases: list[str] | None = None,
    dialect: SqlServerDialect | None = None,
) -> Catalog:
    active = dialect or SqlServerDialect()
    with _cursor(conn) as cursor:
        cursor.execute(
            "SELECT DB_NAME() AS database_name, "
            "CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS version"
        )
        row = cursor.fetchone()
        current = str(row.database_name) if row else ""
        version = str(row.version) if row and row.version else ""

    requested = databases
    if requested and requested[0] in {"*", "all"}:
        requested = _user_databases(conn)
    if requested and len(requested) > 1 and not active.capabilities.cross_database:
        raise ValueError(
            "this dialect does not support multiple databases in one catalog"
        )

    tables: dict[str, Table] = {}
    functions: list[Function] = []
    objects: list[SqlObject] = []
    selected = requested or [current]
    multi = requested is not None and active.capabilities.cross_database
    if not active.capabilities.cross_database:
        if len(selected) > 1:
            raise ValueError(
                "this dialect does not support multiple databases in one catalog"
            )
        if selected[0] != current:
            raise ValueError(
                "this dialect is connected to one database; "
                f"requested {selected[0]!r}, connected to {current!r}"
            )
    try:
        for database_name in selected:
            if active.capabilities.cross_database:
                with _cursor(conn) as cursor:
                    cursor.execute(f"USE [{database_name}]")
            database = database_name if multi else None
            db_tables = _tables(conn, active, database=database)
            _constraints(conn, db_tables)
            _partitions(conn, db_tables)
            _temporal(conn, db_tables, database=database)
            _triggers(conn, db_tables)
            db_functions, db_objects = _objects(
                conn, db_tables, database=database
            )
            tables.update(db_tables)
            functions.extend(db_functions)
            objects.extend(db_objects)
    finally:
        if requested and current and active.capabilities.cross_database:
            try:
                with _cursor(conn) as cursor:
                    cursor.execute(f"USE [{current}]")
            except Exception as exc:  # noqa: BLE001 - restore is best effort
                logger.debug("could not restore database context: %s", exc)

    _infer_relationships(tables)
    rules = _analyze_functions(functions)
    rules.extend(_analyze_triggers(tables))
    return Catalog(
        database=current,
        server_version=version,
        tables=sorted(tables.values(), key=lambda table: table.key),
        schemas=sorted({table.schema for table in tables.values()}),
        functions=functions,
        objects=objects,
        rules=rules,
        databases=sorted(selected) if multi else [],
    )


__all__ = ["SqlServerConnection", "connect", "introspect"]
