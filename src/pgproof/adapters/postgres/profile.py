"""Sampling and profiling of existing data.

Profiles are evidence, not truth. They confirm or reject the invariants we
extracted statically, and they bound the value ranges used by the generator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import psycopg

from ...core.ir import Table, quote_qualified


@dataclass
class ColumnProfile:
    name: str
    null_frac: float = 0.0
    n_distinct: int | None = None
    minimum: Any = None
    maximum: Any = None
    top_values: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TableProfile:
    key: str
    rows: int
    columns: dict[str, ColumnProfile] = field(default_factory=dict)


@dataclass
class Profile:
    tables: dict[str, TableProfile] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": {
                key: {
                    "key": table.key,
                    "rows": table.rows,
                    "columns": {
                        name: {
                            "name": column.name,
                            "null_frac": column.null_frac,
                            "n_distinct": column.n_distinct,
                            "min": _plain(column.minimum),
                            "max": _plain(column.maximum),
                            "top_values": column.top_values,
                        }
                        for name, column in table.columns.items()
                    },
                }
                for key, table in self.tables.items()
            }
        }


def _plain(value: Any) -> Any:
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return None
    return value


def _hashable(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list, set)):
        return repr(value)
    return str(value)


def profile_table(conn: psycopg.Connection, table: Table, sample: int = 1000) -> TableProfile:
    qualified = quote_qualified(table.schema, table.name)
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {qualified}")
        total = int(cur.fetchone()[0])
        cur.execute(f"SELECT * FROM {qualified} LIMIT %s", (sample,))
        names = [description.name for description in cur.description]
        rows = cur.fetchall()

    result = TableProfile(key=table.key, rows=total)
    for index, name in enumerate(names):
        values = [row[index] for row in rows]
        non_null = [value for value in values if value is not None]
        null_frac = 0.0 if not values else 1.0 - (len(non_null) / len(values))

        distinct: set[str] = {_hashable(value) for value in non_null if _hashable(value) is not None}
        counts: dict[str, int] = {}
        for value in non_null:
            key = _hashable(value)
            if key is None:
                continue
            counts[key] = counts.get(key, 0) + 1
        top = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:10]

        minimum = maximum = None
        comparable = [value for value in non_null if isinstance(value, (int, float, Decimal, date, datetime))]
        if comparable:
            try:
                minimum = min(comparable)
                maximum = max(comparable)
            except TypeError:
                minimum = maximum = None

        result.columns[name] = ColumnProfile(
            name=name,
            null_frac=round(null_frac, 4),
            n_distinct=len(distinct),
            minimum=minimum,
            maximum=maximum,
            top_values=[{"value": value, "count": count} for value, count in top],
        )
    return result


def profile_catalog(
    conn: psycopg.Connection, tables: list[Table], sample: int = 1000
) -> Profile:
    profile = Profile()
    for table in tables:
        profile.tables[table.key] = profile_table(conn, table, sample=sample)
    return profile
