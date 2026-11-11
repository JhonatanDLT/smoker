"""Profile model.

Profiles are evidence: aggregates computed from a sample of the source. They are
serializable so a team can commit them next to the spec and reproduce a dataset
without touching production again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from datetime import time as time_value
from decimal import Decimal
from typing import Any

BUCKETS = 20
TOP_K = 20


def hashable(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list, set)):
        return repr(value)
    return str(value)


def is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def bucket_kind(data_kind: Any) -> str:
    from .ir import DataKind

    return {
        DataKind.DATE: "date",
        DataKind.TIMESTAMP: "timestamp",
        DataKind.TIMESTAMPTZ: "timestamptz",
        DataKind.TIME: "time",
    }.get(data_kind, "numeric")


def axis(value: Any, kind: str) -> float:
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


def buckets(values: list[Any], kind: str) -> list[tuple[float, float, int]]:
    numeric = [axis(value, kind) for value in values if value is not None]
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


def restore(value: str) -> Any:
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


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return None
    return value


@dataclass
class ColumnStats:
    name: str
    null_fraction: float = 0.0
    n_distinct: int | None = None
    minimum: Any = None
    maximum: Any = None
    top_values: list[tuple[Any, int]] = field(default_factory=list)
    buckets: list[tuple[float, float, int]] = field(default_factory=list)
    bucket_kind: str = "numeric"
    sampled: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "null_fraction": self.null_fraction,
            "n_distinct": self.n_distinct,
            "minimum": _jsonable(self.minimum),
            "maximum": _jsonable(self.maximum),
            "top_values": [[_jsonable(value), count] for value, count in self.top_values],
            "buckets": [[lo, hi, count] for lo, hi, count in self.buckets],
            "bucket_kind": self.bucket_kind,
            "sampled": self.sampled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ColumnStats:
        return cls(
            name=data.get("name", ""),
            null_fraction=float(data.get("null_fraction", 0.0)),
            n_distinct=data.get("n_distinct"),
            minimum=data.get("minimum"),
            maximum=data.get("maximum"),
            top_values=[
                (value, int(count)) for value, count in data.get("top_values", [])
            ],
            buckets=[
                (float(lo), float(hi), int(count)) for lo, hi, count in data.get("buckets", [])
            ],
            bucket_kind=data.get("bucket_kind", "numeric"),
            sampled=int(data.get("sampled", 0)),
        )


@dataclass
class TableStats:
    key: str
    rows: int
    columns: dict[str, ColumnStats] = field(default_factory=dict)
    sample_method: str = "limit"
    sample_pct: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "rows": self.rows,
            "sample_method": self.sample_method,
            "sample_pct": self.sample_pct,
            "columns": {name: column.to_dict() for name, column in self.columns.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TableStats:
        return cls(
            key=data.get("key", ""),
            rows=int(data.get("rows", 0)),
            columns={
                name: ColumnStats.from_dict(column)
                for name, column in data.get("columns", {}).items()
            },
            sample_method=data.get("sample_method", "limit"),
            sample_pct=data.get("sample_pct"),
        )


@dataclass
class Profile:
    tables: dict[str, TableStats] = field(default_factory=dict)
    generated_at: str | None = None
    sample_size: int = 0
    sample_method: str = "auto"

    def table(self, key: str) -> TableStats | None:
        return self.tables.get(key)

    def column(self, table_key: str, column_name: str) -> ColumnStats | None:
        table = self.tables.get(table_key)
        return table.columns.get(column_name) if table else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "sample_size": self.sample_size,
            "sample_method": self.sample_method,
            "tables": {key: table.to_dict() for key, table in self.tables.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Profile:
        return cls(
            tables={
                key: TableStats.from_dict(table)
                for key, table in data.get("tables", {}).items()
            },
            generated_at=data.get("generated_at"),
            sample_size=int(data.get("sample_size", 0)),
            sample_method=data.get("sample_method", "auto"),
        )
