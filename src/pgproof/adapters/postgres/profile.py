"""PostgreSQL profiling: turn a sample into the core Profile model.

Sampling strategy:

- small tables: ``LIMIT`` (the bias is irrelevant at that size)
- large tables: ``TABLESAMPLE SYSTEM`` with ``REPEATABLE(seed)``
- optional: ``TABLESAMPLE BERNOULLI`` for a uniform sample at a higher cost
- partitioned tables: ``LIMIT``, because TABLESAMPLE is not supported on the
  partitioned parent

Dates, timestamps and times are bucketed on a numeric axis and converted back
during generation.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from datetime import time as time_value
from decimal import Decimal
from typing import Any

import psycopg

from ...core.ir import DataKind, Table, quote_qualified
from ...core.profile import ColumnStats, Profile, TableStats

BUCKETS = 20
TOP_K = 20
LIMIT_THRESHOLD = 50_000


def _hashable(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list, set)):
        return repr(value)
    return str(value)


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _bucket_kind(data_kind: DataKind) -> str:
    return {
        DataKind.DATE: "date",
        DataKind.TIMESTAMP: "timestamp",
        DataKind.TIMESTAMPTZ: "timestamptz",
        DataKind.TIME: "time",
    }.get(data_kind, "numeric")


def _axis(value: Any, kind: str) -> float:
    if kind == "date" and isinstance(value, date) and not isinstance(value, datetime):
        return float(value.toordinal())
    if kind == "timestamp" and isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return (value - datetime(1970, 1, 1)).total_seconds()
    if kind == "timestamptz" and isinstance(value, datetime):
        return value.timestamp()
    if kind == "time" and isinstance(value, time_value):
        return (
            value.hour * 3600
            + value.minute * 60
            + value.second
            + value.microsecond / 1_000_000
        )
    return float(value)


def _buckets(values: list[Any], kind: str) -> list[tuple[float, float, int]]:
    numeric = [_axis(value, kind) for value in values if value is not None]
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


def _sample_rows(
    cur: psycopg.Cursor,
    qualified: str,
    table: Table,
    total: int,
    sample: int,
    method: str,
    seed: int,
) -> tuple[list[tuple[Any, ...]], str, float | None]:
    chosen = method
    if table.is_partitioned:
        # TABLESAMPLE is not supported on a partitioned parent in PostgreSQL.
        chosen = "limit"
    if chosen == "auto":
        chosen = "limit" if total <= LIMIT_THRESHOLD else "system"
    if chosen == "limit" or total <= sample:
        cur.execute(f"SELECT * FROM {qualified} LIMIT %s", (sample,))
        return cur.fetchall(), "limit", None

    pct = max(0.01, min(100.0, 100.0 * sample / max(total, 1)))
    for _ in range(3):
        clause = "SYSTEM" if chosen == "system" else "BERNOULLI"
        cur.execute(
            f"SELECT * FROM {qualified} TABLESAMPLE {clause} ({pct:.4f}) "
            f"REPEATABLE ({int(seed)}) LIMIT %s",
            (sample,),
        )
        rows = cur.fetchall()
        if len(rows) >= max(10, sample // 10):
            return rows, chosen, pct
        pct = min(100.0, pct * 4)
        if pct >= 100.0:
            break

    cur.execute(f"SELECT * FROM {qualified} LIMIT %s", (sample,))
    return cur.fetchall(), "limit", None


def profile_table(
    conn: psycopg.Connection,
    table: Table,
    sample: int = 1000,
    method: str = "auto",
    seed: int = 42,
) -> TableStats:
    qualified = quote_qualified(table.schema, table.name)
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {qualified}")
        total = int(cur.fetchone()[0])
        rows, method_used, pct = _sample_rows(
            cur, qualified, table, total, sample, method, seed
        )
        names = [description.name for description in cur.description]

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
            key = _hashable(value)
            if key is None:
                continue
            distinct.add(key)
            counts[key] = counts.get(key, 0) + 1
        top = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:TOP_K]

        minimum = maximum = None
        comparable = [
            value
            for value in non_null
            if isinstance(value, (int, float, Decimal, date, datetime, time_value))
        ]
        if comparable:
            try:
                minimum = min(comparable)
                maximum = max(comparable)
            except TypeError:
                minimum = maximum = None

        column = table.column(name)
        kind = _bucket_kind(column.data_kind)
        buckets = []
        if column.data_kind in {
            DataKind.SMALLINT,
            DataKind.INT,
            DataKind.BIGINT,
            DataKind.NUMERIC,
            DataKind.FLOAT,
            DataKind.DATE,
            DataKind.TIMESTAMP,
            DataKind.TIMESTAMPTZ,
            DataKind.TIME,
        }:
            buckets = _buckets(non_null, kind)

        result.columns[name] = ColumnStats(
            name=name,
            null_fraction=round(null_fraction, 4),
            n_distinct=len(distinct),
            minimum=minimum,
            maximum=maximum,
            top_values=[(_restore(value), count) for value, count in top],
            buckets=buckets,
            bucket_kind=kind,
            sampled=len(series),
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
            conn, table, sample=sample, method=method, seed=seed
        )
    return profile
