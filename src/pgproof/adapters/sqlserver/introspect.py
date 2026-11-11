"""SQL Server catalog introspection through ``sys.*``.

This is the source-only path: read a SQL Server schema, build the canonical
catalog, and let the application verify it. Generation and dump loading come
later.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Self

from ...core.ir import (
    Catalog,
    CheckConstraint,
    Column,
    ForeignKey,
    Function,
    Provenance,
    SqlObject,
    Table,
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
            "SQL Server support needs pyodbc; install pgproof[sqlserver]"
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


def _tables(conn: Any, dialect: SqlServerDialect) -> dict[str, Table]:
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
    by_object = tables
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
            table = by_object.get(f"{schema_name}.{table_name}")
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
        metadata: dict[tuple[int, int], tuple[str, bool, bool, bool]] = {}
        for row in cursor.fetchall():
            key = (int(row.object_id), int(row.index_id))
            indexes[key].append(str(row.column_name))
            metadata[key] = (
                str(row.name),
                bool(row.is_primary_key),
                bool(row.is_unique),
                bool(row.has_filter),
            )
    for key, columns in indexes.items():
        object_id, _index_id = key
        name, is_primary, is_unique, has_filter = metadata[key]
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
        elif is_unique and not has_filter:
            table.uniques.append(
                UniqueConstraint(
                    name=name,
                    columns=columns,
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


def _objects(conn: Any, tables: dict[str, Table]) -> tuple[list[Function], list[SqlObject]]:
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
                    kind="procedure" if str(row.type) in {"P", "PC"} else "function",
                    language="tsql",
                    provenance=Provenance("catalog", "sys.objects", "function"),
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
                )
            )
    return functions, objects


def introspect(conn: Any) -> Catalog:
    dialect = SqlServerDialect()
    with _cursor(conn) as cursor:
        cursor.execute(
            "SELECT DB_NAME() AS database_name, "
            "CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS version"
        )
        row = cursor.fetchone()
        database = str(row.database_name) if row else ""
        version = str(row.version) if row and row.version else ""

    tables = _tables(conn, dialect)
    _constraints(conn, tables)
    _triggers(conn, tables)
    functions, objects = _objects(conn, tables)
    return Catalog(
        database=database,
        server_version=version,
        tables=sorted(tables.values(), key=lambda table: table.key),
        schemas=sorted({table.schema for table in tables.values()}),
        functions=functions,
        objects=objects,
    )


__all__ = ["SqlServerConnection", "connect", "introspect"]
