from __future__ import annotations

from datetime import date

from pgproof.adapters.postgres.introspect import parse_partition_bound
from pgproof.core.generator import Generator
from pgproof.core.ir import Catalog, Column, Partition, Table
from pgproof.core.planner import build_plan


def test_parse_range_date_bound() -> None:
    partition = parse_partition_bound(
        "FOR VALUES FROM ('2025-01-01') TO ('2025-02-01')",
        Column("created_at", "date"),
    )
    assert partition is not None
    assert partition.strategy == "range"
    assert partition.lower == date(2025, 1, 1)
    assert partition.upper == date(2025, 2, 1)


def test_parse_range_numeric_bound_with_casts() -> None:
    partition = parse_partition_bound(
        "FOR VALUES FROM (1::numeric) TO (100::numeric)",
        Column("amount", "numeric(10,2)"),
    )
    assert partition is not None
    assert partition.lower == 1
    assert partition.upper == 100


def test_parse_list_bound() -> None:
    partition = parse_partition_bound(
        "FOR VALUES IN ('login', 'purchase')", Column("kind", "text")
    )
    assert partition is not None
    assert partition.strategy == "list"
    assert partition.values == ["login", "purchase"]


def test_parse_hash_and_default_bounds() -> None:
    hashed = parse_partition_bound(
        "FOR VALUES WITH (MODULUS 4, REMAINDER 2)", None
    )
    assert hashed is not None
    assert hashed.strategy == "hash"
    assert hashed.modulus == 4
    assert hashed.remainder == 2

    default = parse_partition_bound("DEFAULT", None)
    assert default is not None
    assert default.strategy == "default"


def test_parse_open_range_bounds() -> None:
    partition = parse_partition_bound(
        "FOR VALUES FROM (MINVALUE) TO ('2025-01-01')",
        Column("created_at", "date"),
    )
    assert partition is not None
    assert partition.lower is None
    assert partition.upper == date(2025, 1, 1)


def _range_catalog() -> Catalog:
    table = Table(
        schema="part",
        name="events",
        columns=[
            Column("id", "bigint", nullable=False),
            Column("created_at", "date", nullable=False),
            Column("amount", "numeric(12,2)", nullable=False),
        ],
        primary_key=["id", "created_at"],
        is_partitioned=True,
        partition_strategy="r",
        partition_key=["created_at"],
        partitions=[
            Partition(
                schema="part",
                name="events_2025_01",
                strategy="range",
                lower=date(2025, 1, 1),
                upper=date(2025, 2, 1),
                estimated_rows=100,
            ),
            Partition(
                schema="part",
                name="events_2025_02",
                strategy="range",
                lower=date(2025, 2, 1),
                upper=date(2025, 3, 1),
                estimated_rows=100,
            ),
        ],
    )
    return Catalog(database="x", server_version="16", tables=[table])


def test_generator_respects_range_partitions() -> None:
    catalog = _range_catalog()
    plan = build_plan(catalog, rows=200, seed=3)
    rows = list(Generator(catalog, plan).generate_validated(catalog.table("part.events")))

    assert len(rows) == 200
    for row in rows:
        assert date(2025, 1, 1) <= row[1] < date(2025, 3, 1)
    assert len({(row[0], row[1]) for row in rows}) == 200


def _list_catalog() -> Catalog:
    table = Table(
        schema="part",
        name="audit",
        columns=[
            Column("id", "bigint", nullable=False),
            Column("kind", "text", nullable=False),
        ],
        primary_key=["id", "kind"],
        is_partitioned=True,
        partition_strategy="l",
        partition_key=["kind"],
        partitions=[
            Partition(
                schema="part",
                name="audit_login",
                strategy="list",
                values=["login"],
                estimated_rows=100,
            ),
            Partition(
                schema="part",
                name="audit_purchase",
                strategy="list",
                values=["purchase"],
                estimated_rows=100,
            ),
        ],
    )
    return Catalog(database="x", server_version="16", tables=[table])


def test_generator_respects_list_partitions() -> None:
    catalog = _list_catalog()
    plan = build_plan(catalog, rows=100, seed=4)
    rows = list(Generator(catalog, plan).generate_validated(catalog.table("part.audit")))

    assert {row[1] for row in rows} <= {"login", "purchase"}
    assert len(rows) == 100


def test_hash_partitioning_does_not_restrict_values() -> None:
    table = Table(
        schema="part",
        name="metrics",
        columns=[
            Column("id", "bigint", nullable=False),
            Column("app_id", "integer", nullable=False),
        ],
        primary_key=["id", "app_id"],
        is_partitioned=True,
        partition_strategy="h",
        partition_key=["app_id"],
        partitions=[
            Partition(schema="part", name="metrics_0", strategy="hash", modulus=4, remainder=0)
        ],
    )
    catalog = Catalog(database="x", server_version="16", tables=[table])
    plan = build_plan(catalog, rows=50, seed=5)
    rows = list(Generator(catalog, plan).generate_validated(table))
    assert len(rows) == 50
    assert all(isinstance(row[1], int) for row in rows)
