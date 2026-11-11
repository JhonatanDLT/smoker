"""SQL Server profiling: turn a TOP/TABLESAMPLE sample into a Profile."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from ...core.ir import DataKind
from ...core.profile import (
    TOP_K,
    ColumnStats,
    Profile,
    TableStats,
    bucket_kind,
    buckets,
    hashable,
    restore,
)
from .dialect import SqlServerDialect
from .introspect import _cursor

LIMIT_THRESHOLD = 50_000

BUCKET_KINDS = {
    DataKind.SMALLINT,
    DataKind.INT,
    DataKind.BIGINT,
    DataKind.NUMERIC,
    DataKind.FLOAT,
    DataKind.DATE,
    DataKind.TIMESTAMP,
    DataKind.TIMESTAMPTZ,
    DataKind.TIME,
}


def _sample_rows(
    cursor: Any,
    qualified: str,
    table: Any,
    total: int,
    sample: int,
    method: str,
    seed: int,
) -> tuple[list[Any], str, float | None]:
    chosen = method
    if chosen == "auto":
        chosen = "limit" if total <= LIMIT_THRESHOLD else "tablesample"
    if chosen == "limit" or total <= sample:
        cursor.execute(f"SELECT TOP ({int(sample)}) * FROM {qualified}")
        return cursor.fetchall(), "limit", None

    pct = max(0.01, min(100.0, 100.0 * sample / max(total, 1)))
    for _ in range(3):
        cursor.execute(
            f"SELECT TOP ({int(sample)}) * FROM {qualified} "
            f"TABLESAMPLE SYSTEM ({pct:.4f} PERCENT) REPEATABLE ({int(seed)})"
        )
        rows = cursor.fetchall()
        if len(rows) >= max(10, sample // 10):
            return rows, "tablesample", pct
        pct = min(100.0, pct * 4)
        if pct >= 100.0:
            break
    cursor.execute(f"SELECT TOP ({int(sample)}) * FROM {qualified}")
    return cursor.fetchall(), "limit", None


def profile_table(
    connection: Any,
    table: Any,
    sample: int = 1000,
    method: str = "auto",
    seed: int = 42,
) -> TableStats:
    dialect = SqlServerDialect()
    qualified = dialect.quote_qualified(table.schema, table.name)
    with _cursor(connection) as cursor:
        cursor.execute(f"SELECT COUNT_BIG(*) FROM {qualified}")
        total = int(cursor.fetchone()[0])
        rows, method_used, pct = _sample_rows(
            cursor, qualified, table, total, sample, method, seed
        )
        names = [description[0] for description in cursor.description]

    result = TableStats(
        key=table.key,
        rows=total,
        sample_method=method_used,
        sample_pct=pct,
    )
    for index, name in enumerate(names):
        series = [row[index] for row in rows]
        non_null = [value for value in series if value is not None]
        null_fraction = 0.0 if not series else 1.0 - (len(non_null) / len(series))
        counts: dict[str, int] = {}
        distinct: set[str] = set()
        for value in non_null:
            key = hashable(value)
            if key is None:
                continue
            distinct.add(key)
            counts[key] = counts.get(key, 0) + 1
        top = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:TOP_K]

        minimum = maximum = None
        comparable = [
            value
            for value in non_null
            if isinstance(value, (int, float, Decimal, date, datetime, time))
        ]
        if comparable:
            try:
                minimum = min(comparable)
                maximum = max(comparable)
            except TypeError:
                minimum = maximum = None

        column = table.column(name)
        kind = bucket_kind(column.data_kind)
        bucket_values = (
            buckets(non_null, kind)
            if column.data_kind in BUCKET_KINDS
            else []
        )
        result.columns[name] = ColumnStats(
            name=name,
            null_fraction=round(null_fraction, 4),
            n_distinct=len(distinct),
            minimum=minimum,
            maximum=maximum,
            top_values=[(restore(value), count) for value, count in top],
            buckets=bucket_values,
            bucket_kind=kind,
            sampled=len(series),
        )
    return result


def profile_catalog(
    connection: Any,
    tables: list[Any],
    sample: int = 1000,
    method: str = "auto",
    seed: int = 42,
) -> Profile:
    profile = Profile(
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        sample_size=sample,
        sample_method=method,
    )
    for table in tables:
        profile.tables[table.key] = profile_table(
            connection, table, sample=sample, method=method, seed=seed
        )
    return profile


__all__ = ["profile_catalog", "profile_table"]
