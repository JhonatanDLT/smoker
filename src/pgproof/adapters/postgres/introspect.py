"""PostgreSQL introspection and profiling.

Reads pg_catalog directly. A dump is a lossy copy of what the database already
knows, so we never parse DDL when a live connection is available.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg

from ...core.ir import (
    Catalog,
    CheckConstraint,
    Column,
    DataKind,
    ForeignKey,
    Partition,
    Table,
    Trigger,
    UniqueConstraint,
    quote_qualified,
)

SYSTEM_SCHEMAS = {"pg_catalog", "information_schema"}

_TABLES_SQL = """
SELECT n.nspname AS schema,
       c.relname AS name,
       c.relkind::text AS relkind,
       c.reltuples::bigint AS estimated_rows,
       c.relispartition AS is_partition
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg_toast%%'
ORDER BY n.nspname, c.relname
"""

_COLUMNS_SQL = """
SELECT n.nspname AS schema,
       c.relname AS table_name,
       a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS sql_type,
       NOT a.attnotnull AS nullable,
       pg_get_expr(d.adbin, d.adrelid) AS default_expr,
       a.attidentity::text AS identity,
       a.attgenerated::text AS generated,
       a.atttypid::bigint AS type_oid,
       t.typtype::text AS type_kind,
       col_description(c.oid, a.attnum) AS comment
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_type t ON t.oid = a.atttypid
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE a.attnum > 0
  AND NOT a.attisdropped
  AND c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg_toast%%'
ORDER BY n.nspname, c.relname, a.attnum
"""

_ENUMS_SQL = """
SELECT t.oid::bigint AS type_oid,
       array_agg(e.enumlabel ORDER BY e.enumsortorder) AS labels
FROM pg_type t
JOIN pg_enum e ON e.enumtypid = t.oid
GROUP BY t.oid
"""

_CONSTRAINTS_SQL = """
SELECT n.nspname AS schema,
       c.relname AS table_name,
       con.conname AS name,
       con.contype::text AS kind,
       pg_get_constraintdef(con.oid, true) AS definition,
       con.convalidated AS validated,
       con.conkey AS child_attnums,
       con.confkey AS parent_attnums,
       rn.nspname AS ref_schema,
       rc.relname AS ref_table
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_class rc ON rc.oid = con.confrelid
LEFT JOIN pg_namespace rn ON rn.oid = rc.relnamespace
WHERE con.contype IN ('p', 'f', 'u', 'c', 'x')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg_toast%%'
ORDER BY n.nspname, c.relname, con.conname
"""

_TRIGGERS_SQL = """
SELECT n.nspname AS schema,
       c.relname AS table_name,
       t.tgname AS name,
       t.tgtype::int AS tgtype,
       p.proname AS function_name,
       pg_get_triggerdef(t.oid, true) AS definition,
       pg_get_functiondef(p.oid) AS body
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_proc p ON p.oid = t.tgfoid
WHERE NOT t.tgisinternal
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg_toast%%'
ORDER BY n.nspname, c.relname, t.tgname
"""

_PARTITIONED_SQL = """
SELECT parent_ns.nspname AS schema,
       parent.relname AS table_name,
       pt.partstrat::text AS strategy,
       (
         SELECT array_agg(a.attname ORDER BY u.ord)
         FROM unnest(pt.partattrs) WITH ORDINALITY AS u(attnum, ord)
         JOIN pg_attribute a
           ON a.attrelid = pt.partrelid AND a.attnum = u.attnum
       ) AS key_columns
FROM pg_partitioned_table pt
JOIN pg_class parent ON parent.oid = pt.partrelid
JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
WHERE parent_ns.nspname NOT IN ('pg_catalog', 'information_schema')
"""

_PARTITIONS_SQL = """
SELECT parent_ns.nspname AS parent_schema,
       parent.relname AS parent_table,
       child_ns.nspname AS child_schema,
       child.relname AS child_table,
       child.relkind::text AS child_kind,
       child.reltuples::bigint AS estimated_rows,
       pg_get_expr(child.relpartbound, child.oid) AS bound
FROM pg_inherits i
JOIN pg_class parent ON parent.oid = i.inhparent
JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
JOIN pg_class child ON child.oid = i.inhrelid
JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace
WHERE parent.relkind = 'p'
  AND parent_ns.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY parent_ns.nspname, parent.relname, child.relname
"""


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True)


def server_identity(conn: psycopg.Connection) -> tuple[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_setting('server_version')")
        database, version = cur.fetchone()
    return database, version


def introspect(conn: psycopg.Connection) -> Catalog:
    database, version = server_identity(conn)

    tables: dict[str, Table] = {}
    with conn.cursor() as cur:
        cur.execute(_TABLES_SQL)
        for schema, name, _relkind, estimated_rows, is_partition in cur.fetchall():
            if is_partition:
                continue
            tables[f"{schema}.{name}"] = Table(
                schema=schema, name=name, estimated_rows=int(estimated_rows or 0)
            )

    enum_labels: dict[int, list[str]] = {}
    with conn.cursor() as cur:
        cur.execute(_ENUMS_SQL)
        for type_oid, labels in cur.fetchall():
            enum_labels[int(type_oid)] = list(labels)

    attnum_maps: dict[str, dict[int, str]] = {}

    with conn.cursor() as cur:
        cur.execute(_COLUMNS_SQL)
        for row in cur.fetchall():
            (
                schema,
                table_name,
                column_name,
                sql_type,
                nullable,
                default_expr,
                identity,
                generated,
                type_oid,
                _type_kind,
                comment,
            ) = row
            key = f"{schema}.{table_name}"
            table = tables.get(key)
            if table is None:
                continue
            labels = enum_labels.get(int(type_oid), [])
            table.columns.append(
                Column(
                    name=column_name,
                    sql_type=sql_type,
                    nullable=bool(nullable),
                    default=default_expr,
                    identity=identity if identity in {"a", "d"} else None,
                    generated=generated if generated in {"s", "v"} else None,
                    enum_values=labels,
                    comment=comment,
                )
            )
            attnum_maps.setdefault(key, {})

    # Rebuild attnum maps from a dedicated query so composite keys resolve.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT n.nspname, c.relname, a.attnum, a.attname
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE a.attnum > 0 AND NOT a.attisdropped
              AND c.relkind IN ('r', 'p')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
            """
        )
        for schema, table_name, attnum, attname in cur.fetchall():
            attnum_maps.setdefault(f"{schema}.{table_name}", {})[int(attnum)] = attname

    with conn.cursor() as cur:
        cur.execute(_CONSTRAINTS_SQL)
        for row in cur.fetchall():
            (
                schema,
                table_name,
                name,
                kind,
                definition,
                validated,
                child_attnums,
                parent_attnums,
                ref_schema,
                ref_table,
            ) = row
            key = f"{schema}.{table_name}"
            table = tables.get(key)
            if table is None:
                continue
            attmap = attnum_maps.get(key, {})
            child_columns = [attmap.get(int(num), str(num)) for num in (child_attnums or [])]

            if kind == "p":
                table.primary_key = child_columns
            elif kind == "u":
                table.uniques.append(UniqueConstraint(name=name, columns=child_columns))
            elif kind == "c":
                table.checks.append(
                    CheckConstraint(name=name, definition=definition, validated=bool(validated))
                )
            elif kind == "x":
                table.checks.append(
                    CheckConstraint(
                        name=name,
                        definition=definition,
                        predicate=None,
                        validated=bool(validated),
                    )
                )
            elif kind == "f":
                parent_key = f"{ref_schema}.{ref_table}"
                parent_map = attnum_maps.get(parent_key, {})
                parent_columns = [
                    parent_map.get(int(num), str(num)) for num in (parent_attnums or [])
                ]
                table.foreign_keys.append(
                    ForeignKey(
                        name=name,
                        child_schema=schema,
                        child_table=table_name,
                        child_columns=child_columns,
                        parent_schema=ref_schema,
                        parent_table=ref_table,
                        parent_columns=parent_columns,
                    )
                )

    with conn.cursor() as cur:
        cur.execute(_TRIGGERS_SQL)
        for row in cur.fetchall():
            schema, table_name, name, tgtype, function_name, definition, body = row
            table = tables.get(f"{schema}.{table_name}")
            if table is None:
                continue
            table.triggers.append(
                Trigger(
                    name=name,
                    timing=_trigger_timing(tgtype),
                    events=_trigger_events(tgtype),
                    function=function_name,
                    definition=definition,
                    body=(body or "")[:20000],
                )
            )

    _attach_partitions(conn, tables)

    return Catalog(
        database=database,
        server_version=version,
        tables=sorted(tables.values(), key=lambda table: table.key),
        schemas=sorted({table.schema for table in tables.values()}),
    )


def _trigger_timing(tgtype: int) -> str:
    # Trigger type bits: 1=ROW, 2=BEFORE, 4=INSERT, 8=DELETE, 16=UPDATE, 32=TRUNCATE,
    # 64=INSTEAD OF. See PostgreSQL docs on pg_trigger.tgtype.
    if tgtype & 64:
        return "INSTEAD OF"
    if tgtype & 2:
        return "BEFORE"
    return "AFTER"


def _trigger_events(tgtype: int) -> str:
    events = []
    if tgtype & 4:
        events.append("INSERT")
    if tgtype & 16:
        events.append("UPDATE")
    if tgtype & 8:
        events.append("DELETE")
    if tgtype & 32:
        events.append("TRUNCATE")
    return " OR ".join(events) or "UNKNOWN"


def _attach_partitions(conn: psycopg.Connection, tables: dict[str, Table]) -> None:
    metadata: dict[str, tuple[str, list[str]]] = {}
    with conn.cursor() as cur:
        cur.execute(_PARTITIONED_SQL)
        for schema, name, strategy, key_columns in cur.fetchall():
            metadata[f"{schema}.{name}"] = (strategy, list(key_columns or []))

    for key, (strategy, key_columns) in metadata.items():
        table = tables.get(key)
        if table is None:
            continue
        table.is_partitioned = True
        table.partition_strategy = strategy
        table.partition_key = key_columns

    if not metadata:
        return

    with conn.cursor() as cur:
        cur.execute(_PARTITIONS_SQL)
        for row in cur.fetchall():
            (
                parent_schema,
                parent_table,
                child_schema,
                child_table,
                child_kind,
                estimated_rows,
                bound,
            ) = row
            table = tables.get(f"{parent_schema}.{parent_table}")
            if table is None:
                continue
            column = (
                table.column(table.partition_key[0])
                if len(table.partition_key) == 1
                else None
            )
            partition = parse_partition_bound(bound or "", column)
            if partition is None:
                continue
            partition.schema = child_schema
            partition.name = child_table
            partition.estimated_rows = max(0, int(estimated_rows or 0))
            table.partitions.append(partition)
            if child_kind == "p":
                table.has_sub_partitions = True


_RANGE_BOUND = re.compile(r"FOR VALUES FROM \((.*?)\) TO \((.*?)\)$", re.DOTALL)
_LIST_BOUND = re.compile(r"FOR VALUES IN \((.*)\)$", re.DOTALL)
_HASH_BOUND = re.compile(
    r"FOR VALUES WITH \(MODULUS (\d+), REMAINDER (\d+)\)", re.IGNORECASE
)
_CAST = re.compile(r"^(.*?)::[\w\s\[\]\.\"']+$", re.DOTALL)


def parse_partition_bound(bound: str, column: Column | None) -> Partition | None:
    text = " ".join(bound.split())
    if not text:
        return None
    if text.upper() == "DEFAULT":
        return Partition(schema="", name="", strategy="default")

    match = _RANGE_BOUND.search(text)
    if match:
        return Partition(
            schema="",
            name="",
            strategy="range",
            lower=_parse_literal(match.group(1), column),
            upper=_parse_literal(match.group(2), column),
        )

    match = _LIST_BOUND.search(text)
    if match:
        values = [
            _parse_literal(part, column)
            for part in _split_top_level(match.group(1))
        ]
        return Partition(schema="", name="", strategy="list", values=values)

    match = _HASH_BOUND.search(text)
    if match:
        return Partition(
            schema="",
            name="",
            strategy="hash",
            modulus=int(match.group(1)),
            remainder=int(match.group(2)),
        )
    return None


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_quote = False
    for char in text:
        if char == "'":
            in_quote = not in_quote
            current.append(char)
        elif not in_quote and char == "(":
            depth += 1
            current.append(char)
        elif not in_quote and char == ")":
            depth -= 1
            current.append(char)
        elif not in_quote and depth == 0 and char == ",":
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current).strip())
    return [part for part in parts if part]


def _parse_literal(text: str, column: Column | None) -> Any:
    value = text.strip()
    if value.upper() in {"MINVALUE", "MAXVALUE"}:
        return None
    cast = _CAST.match(value)
    if cast:
        value = cast.group(1).strip()
    if value.startswith("'") and value.endswith("'"):
        value = value[1:-1].replace("''", "'")
    if column is None:
        return value
    kind = column.data_kind
    try:
        if kind == DataKind.DATE:
            return date.fromisoformat(value.split(" ")[0])
        if kind in (DataKind.TIMESTAMP, DataKind.TIMESTAMPTZ):
            parsed = datetime.fromisoformat(value.replace(" ", "T"))
            if kind == DataKind.TIMESTAMPTZ and parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
        if kind in (DataKind.SMALLINT, DataKind.INT, DataKind.BIGINT):
            return int(value)
        if kind == DataKind.NUMERIC:
            return Decimal(value)
        if kind == DataKind.FLOAT:
            return float(value)
        if kind == DataKind.BOOLEAN:
            return value.lower() in {"true", "t", "1"}
    except (ValueError, InvalidOperation):
        return value
    return value


def table_counts(conn: psycopg.Connection, table_keys: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for key in table_keys:
            schema, name = key.split(".", 1)
            cur.execute(f"SELECT count(*) FROM {quote_qualified(schema, name)}")
            counts[key] = int(cur.fetchone()[0])
    return counts


def extra_metadata(conn: psycopg.Connection) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SHOW TimeZone")
        timezone = cur.fetchone()[0]
        cur.execute("SHOW server_encoding")
        encoding = cur.fetchone()[0]
    return {"timezone": timezone, "encoding": encoding}
