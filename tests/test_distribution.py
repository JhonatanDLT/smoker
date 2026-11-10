from __future__ import annotations

import random
from decimal import Decimal

from pgproof.core.distribution import (
    BooleanDistribution,
    CategoricalDistribution,
    NumericDistribution,
    build_distribution,
    coerce_value,
)
from pgproof.core.generator import Generator
from pgproof.core.ir import Catalog, Column, Table
from pgproof.core.planner import build_plan
from pgproof.core.profile import ColumnStats, Profile, TableStats


def test_categorical_distribution_respects_weights() -> None:
    distribution = CategoricalDistribution(["a", "b", "c"], [70, 20, 10])
    rng = random.Random(1)
    samples = [distribution.sample(rng) for _ in range(10_000)]
    assert abs(samples.count("a") / len(samples) - 0.70) < 0.03
    assert abs(samples.count("b") / len(samples) - 0.20) < 0.03
    assert abs(samples.count("c") / len(samples) - 0.10) < 0.03


def test_numeric_distribution_stays_in_range() -> None:
    distribution = NumericDistribution([(1.0, 10.0, 500), (10.0, 20.0, 500)])
    rng = random.Random(2)
    samples = [distribution.sample(rng) for _ in range(2_000)]
    assert all(1.0 <= value <= 20.0 for value in samples)


def test_boolean_distribution() -> None:
    distribution = BooleanDistribution(true_fraction=0.8)
    rng = random.Random(3)
    samples = [distribution.sample(rng) for _ in range(5_000)]
    assert abs(sum(samples) / len(samples) - 0.8) < 0.03


def test_build_distribution_ignores_small_samples() -> None:
    stats = ColumnStats(name="status", n_distinct=2, top_values=[("a", 1)], sampled=10)
    column = Column("status", "text")
    assert build_distribution(stats, column) is None


def test_build_distribution_prefers_categorical() -> None:
    stats = ColumnStats(
        name="status", n_distinct=2, top_values=[("a", 7), ("b", 3)], sampled=100
    )
    distribution = build_distribution(stats, Column("status", "text"))
    assert isinstance(distribution, CategoricalDistribution)


def test_build_distribution_uses_buckets_for_numeric() -> None:
    stats = ColumnStats(
        name="amount",
        n_distinct=100,
        top_values=[("1", 1)],
        buckets=[(1.0, 10.0, 5), (10.0, 20.0, 5)],
        sampled=100,
    )
    distribution = build_distribution(stats, Column("amount", "numeric"))
    assert isinstance(distribution, NumericDistribution)


def test_coerce_value_casts_to_column_type() -> None:
    assert coerce_value(Column("n", "integer"), "42") == 42
    assert coerce_value(Column("n", "numeric"), "1.5") == Decimal("1.5")
    assert coerce_value(Column("n", "double precision"), "1.5") == 1.5


def test_coerce_value_quantizes_to_the_column_scale() -> None:
    column = Column("n", "numeric(10,2)")
    assert coerce_value(column, 99919.29865494293) == Decimal("99919.30")


def test_coerce_value_dates_and_times() -> None:
    from datetime import UTC, date, datetime, time

    assert coerce_value(Column("d", "date"), date(2025, 1, 1).toordinal()) == date(
        2025, 1, 1
    )
    assert coerce_value(Column("d", "date"), "2025-01-01") == date(2025, 1, 1)
    assert coerce_value(Column("t", "time"), 3661.0) == time(1, 1, 1)
    assert coerce_value(
        Column("ts", "timestamp with time zone"),
        datetime(2025, 1, 1, tzinfo=UTC).timestamp(),
    ) == datetime(2025, 1, 1, tzinfo=UTC)


def test_build_distribution_accepts_date_buckets() -> None:
    stats = ColumnStats(
        name="created_at",
        n_distinct=365,
        top_values=[("2025-01-01", 1)],
        buckets=[
            (738886.0, 738904.0, 50),
            (738904.0, 738922.0, 50),
        ],
        bucket_kind="date",
        sampled=100,
    )
    distribution = build_distribution(stats, Column("created_at", "date"))
    assert isinstance(distribution, NumericDistribution)


def test_profile_round_trip() -> None:
    profile = Profile(
        tables={
            "public.events": TableStats(
                key="public.events",
                rows=100,
                columns={
                    "status": ColumnStats(
                        name="status",
                        n_distinct=2,
                        top_values=[("a", 7), ("b", 3)],
                        sampled=10,
                    )
                },
            )
        },
        sample_size=100,
    )
    restored = Profile.from_dict(profile.to_dict())
    stats = restored.tables["public.events"].columns["status"]
    assert stats.top_values == [("a", 7), ("b", 3)]
    assert stats.n_distinct == 2


def test_generator_uses_the_profile() -> None:
    table = Table(
        schema="public",
        name="events",
        columns=[
            Column("id", "integer", nullable=False),
            Column("status", "text", nullable=False),
        ],
        primary_key=["id"],
    )
    catalog = Catalog(database="x", server_version="16", tables=[table])
    profile = Profile(
        tables={
            "public.events": TableStats(
                key="public.events",
                rows=1000,
                columns={
                    "status": ColumnStats(
                        name="status",
                        n_distinct=3,
                        top_values=[("a", 700), ("b", 200), ("c", 100)],
                        sampled=1000,
                    )
                },
            )
        }
    )
    plan = build_plan(catalog, rows=2000, seed=4)
    rows = list(Generator(catalog, plan, profile=profile).generate_validated(table))
    statuses = [row[1] for row in rows]
    assert abs(statuses.count("a") / len(statuses) - 0.70) < 0.04
    assert abs(statuses.count("b") / len(statuses) - 0.20) < 0.04
