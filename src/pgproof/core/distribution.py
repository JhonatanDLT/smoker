"""Distributions derived from a profile.

The generator consults a distribution before falling back to semantic rules.
Constraint correctness always wins: after sampling, the value still goes through
the local rule engine.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .ir import Column, DataKind
from .profile import ColumnStats

_NUMERIC_SCALE = re.compile(r"numeric\s*\(\s*\d+\s*,\s*(\d+)\s*\)", re.IGNORECASE)
_NUMERIC_KINDS = {
    DataKind.SMALLINT,
    DataKind.INT,
    DataKind.BIGINT,
    DataKind.NUMERIC,
    DataKind.FLOAT,
}


@dataclass
class CategoricalDistribution:
    values: list[Any]
    weights: list[float]
    null_fraction: float = 0.0

    def sample(self, rng: random.Random) -> Any:
        if self.null_fraction > 0 and rng.random() < self.null_fraction:
            return None
        return rng.choices(self.values, weights=self.weights, k=1)[0]


@dataclass
class NumericDistribution:
    buckets: list[tuple[float, float, int]]
    null_fraction: float = 0.0

    def sample(self, rng: random.Random) -> Any:
        if self.null_fraction > 0 and rng.random() < self.null_fraction:
            return None
        lo, hi, _ = rng.choices(self.buckets, weights=[b[2] for b in self.buckets], k=1)[0]
        if hi <= lo:
            return lo
        return rng.uniform(lo, hi)


@dataclass
class BooleanDistribution:
    true_fraction: float
    null_fraction: float = 0.0

    def sample(self, rng: random.Random) -> Any:
        if self.null_fraction > 0 and rng.random() < self.null_fraction:
            return None
        return rng.random() < self.true_fraction


Distribution = CategoricalDistribution | NumericDistribution | BooleanDistribution


def build_distribution(
    stats: ColumnStats,
    column: Column,
    min_sample: int = 50,
) -> Distribution | None:
    if stats.sampled < min_sample:
        return None

    if column.data_kind == DataKind.BOOLEAN:
        true_count = 0
        total = 0
        for value, count in stats.top_values:
            if str(value).lower() in {"true", "t", "1"}:
                true_count += count
            total += count
        if total == 0:
            return None
        return BooleanDistribution(true_count / total, stats.null_fraction)

    if column.enum_values or (
        stats.n_distinct is not None
        and stats.n_distinct <= 20
        and len(stats.top_values) >= 2
    ):
        values = [value for value, _ in stats.top_values]
        weights = [max(count, 0) for _, count in stats.top_values]
        if sum(weights) <= 0:
            return None
        return CategoricalDistribution(values, weights, stats.null_fraction)

    if column.data_kind in _NUMERIC_KINDS and len(stats.buckets) >= 2:
        return NumericDistribution(stats.buckets, stats.null_fraction)

    return None


def coerce_value(column: Column, value: Any) -> Any:
    """Bring a sampled value back into the column's Python type."""
    if value is None:
        return None
    kind = column.data_kind
    try:
        if kind in (DataKind.SMALLINT, DataKind.INT, DataKind.BIGINT):
            return int(value)
        if kind == DataKind.NUMERIC:
            decimal_value = value if isinstance(value, Decimal) else _decimal(value)
            if not isinstance(decimal_value, Decimal):
                return value
            scale = numeric_scale(column.sql_type)
            return decimal_value.quantize(Decimal(1).scaleb(-scale))
        if kind == DataKind.FLOAT:
            return float(value)
        if kind == DataKind.TEXT and not isinstance(value, str):
            return str(value)
    except (TypeError, ValueError):
        return value
    return value


def _decimal(value: Any) -> Any:
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError):
        return value


def numeric_scale(sql_type: str) -> int:
    match = _NUMERIC_SCALE.search(sql_type)
    if match:
        return int(match.group(1))
    return 6
