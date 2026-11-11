"""Generate SQL Server temporal history with a deterministic DML scenario.

SQL Server writes history rows automatically on UPDATE and DELETE. This module
chooses one safe column per temporal table and updates it once, so the history
table receives one version per current row without touching period columns.
"""

from __future__ import annotations

from typing import Any

from ...core.invariants import parse_local_rules
from ...core.ir import DataKind, Table
from .dialect import SqlServerDialect
from .introspect import _cursor


def _safe_column(table: Table) -> Any | None:
    rules = parse_local_rules(table)
    blocked = set(table.primary_key)
    blocked.update(
        column for fk in table.foreign_keys for column in fk.child_columns
    )
    blocked.update(
        column for unique in table.uniques for column in unique.columns
    )
    blocked.update(rules.ranges)
    blocked.update(rules.allowed)
    blocked.update(rules.maxlen)
    blocked.update(left for left, _right, _strict in rules.orderings)
    blocked.update(right for _left, right, _strict in rules.orderings)
    for target, left, operator, right in rules.derived:
        blocked.update((target, left))
        if operator != "copy":
            blocked.add(right)
    check_text = " ".join(
        check.predicate or "" for check in table.checks
    ).lower()

    candidates = []
    for column in table.writable_columns:
        if column.name in blocked or column.generated or column.identity:
            continue
        if column.enum_values:
            continue
        if column.name.lower() in check_text:
            continue
        if column.data_kind in {
            DataKind.TEXT,
            DataKind.INT,
            DataKind.SMALLINT,
            DataKind.BIGINT,
            DataKind.NUMERIC,
            DataKind.FLOAT,
            DataKind.DATE,
            DataKind.TIMESTAMP,
            DataKind.TIMESTAMPTZ,
            DataKind.BOOLEAN,
        }:
            candidates.append(column)
    if not candidates:
        return None
    priority = {
        DataKind.TEXT: 0,
        DataKind.NUMERIC: 1,
        DataKind.INT: 2,
        DataKind.SMALLINT: 2,
        DataKind.BIGINT: 2,
        DataKind.FLOAT: 2,
        DataKind.DATE: 3,
        DataKind.TIMESTAMP: 3,
        DataKind.TIMESTAMPTZ: 3,
        DataKind.BOOLEAN: 4,
    }
    return min(
        candidates,
        key=lambda item: (priority.get(item.data_kind, 9), item.name),
    )


def _expression(column: Any, quote: Any) -> str:
    name = quote(column.name)
    if column.data_kind == DataKind.TEXT:
        limit = column.max_length
        base = f"ISNULL({name}, N'') + N' (history)'"
        return f"LEFT({base}, {limit})" if limit else base
    if column.data_kind in {
        DataKind.INT,
        DataKind.SMALLINT,
        DataKind.BIGINT,
        DataKind.NUMERIC,
        DataKind.FLOAT,
    }:
        return f"ISNULL({name}, 0) + 1"
    if column.data_kind in {
        DataKind.DATE,
        DataKind.TIMESTAMP,
        DataKind.TIMESTAMPTZ,
    }:
        return f"DATEADD(day, 1, ISNULL({name}, GETDATE()))"
    if column.data_kind == DataKind.BOOLEAN:
        return f"CASE WHEN {name} = 1 THEN 0 ELSE 1 END"
    return name


def populate_temporal_history(
    connection: Any,
    catalog: Any,
    dialect: SqlServerDialect | None = None,
) -> int:
    active = dialect or SqlServerDialect()
    updated = 0
    with _cursor(connection) as cursor:
        for table in catalog.tables:
            if table.temporal is None:
                continue
            column = _safe_column(table)
            if column is None:
                continue
            expression = _expression(column, active.quote_ident)
            cursor.execute(
                f"UPDATE {active.quote_table(table)} "
                f"SET {active.quote_ident(column.name)} = {expression}"
            )
            updated += max(0, cursor.rowcount)
    connection.commit()
    return updated


__all__ = ["populate_temporal_history"]
