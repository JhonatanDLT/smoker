"""PostgreSQL profiling: turn a sample into the core Profile model."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg

from ...core.ir import DataKind, Table, quote_qualified
from ...core.profile import ColumnStats, Profile, TableStats

BUCKETS = 20
TOP_K = 20


def _hashable(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list, set)):
        return repr(value)
    return str(value)


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _buckets(values: list[Any]) -> list[tuple[float, float, int]]:
    numeric = [float(value) for value in values if _is_numeric(value)]
    if len(numeric) < BUCKETS:
        return []
    low, high = min(numeric), max(numeric)
    if high <= low:
        return []
    width = (high - low) / BUCKETS
    counts = [0] * BUCKETS
    for value in numeric:
        index = min(int((value - low) / width), BUCKETS - 1)
        counts[index] += 1
    return [
        (low + index * width, low + (index + 1) * width, count)
        for index, count in enumerate(counts)
        if count > 0
    ]


def profile_table(conn: psycopg.Connection, table: Table, sample: int = 1000) -> TableStats:
    qualified = quote_qualified(table.schema, table.name)
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {qualified}")
        total = int(cur.fetchone()[0])
        cur.execute(f"SELECT * FROM {qualified} LIMIT %s", (sample,))
        names = [description.name for description in cur.description]
        rows = cur.fetchall()

    result = TableStats(key=table.key, rows=total)
    for index, name in enumerate(names):
        values = [row[index] for row in rows]
        non_null = [value for value in values if value is not None]
        null_fraction = 0.0 if not values else 1.0 - (len(non_null) / len(values))

        counts: dict[str, int] = {}
        distinct: set[str] = set()
        for value in non_null:
            key = _hashable(value)
            if key is None:
                continue
            distinct.add(key)
            counts[key] = counts.get(key, 0) + 1
        top = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:TOP_K]

        minimum = maximum = None
        comparable = [
            value for value in non_null if isinstance(value, (int, float, Decimal, date, datetime))
        ]
        if comparable:
            try:
                minimum = min(comparable)
                maximum = max(comparable)
            except TypeError:
                minimum = maximum = None

        column = table.column(name)
        buckets = (
            _buckets(non_null)
            if column.data_kind in {
                DataKind.SMALLINT,
                DataKind.INT,
                DataKind.BIGINT,
                DataKind.NUMERIC,
                DataKind.FLOAT,
            }
            else []
        )

        result.columns[name] = ColumnStats(
            name=name,
            null_fraction=round(null_fraction, 4),
            n_distinct=len(distinct),
            minimum=minimum,
            maximum=maximum,
            top_values=[(_restore(value), count) for value, count in top],
            buckets=buckets,
            sampled=len(values),
        )
    return result


def _restore(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "none":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def profile_catalog(
    conn: psycopg.Connection,
    tables: list[Table],
    sample: int = 1000,
) -> Profile:

    profile = Profile(
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        sample_size=sample,
    )
    for table in tables:
        profile.tables[table.key] = profile_table(conn, table, sample=sample)
    return profile
