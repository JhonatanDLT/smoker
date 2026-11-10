"""Profile model.

Profiles are evidence: aggregates computed from a sample of the source. They are
serializable so a team can commit them next to the spec and reproduce a dataset
without touching production again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any


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
