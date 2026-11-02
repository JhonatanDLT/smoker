"""PostgreSQL introspection and profiling.

Reads pg_catalog directly. A dump is a lossy copy of what the database already
knows, so we never parse DDL when a live connection is available.
"""

from __future__ import annotations

from typing import Any

import psycopg

from ...core.ir import (
    Catalog,
    CheckConstraint,
    Column,
    ForeignKey,
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
       c.reltuples::bigint AS estimated_rows
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
        for schema, name, _relkind, estimated_rows in cur.fetchall():
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
