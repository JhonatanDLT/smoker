from __future__ import annotations

from decimal import Decimal

from pgproof.core.generator import Generator
from pgproof.core.ir import Catalog, CheckConstraint, Column, Table
from pgproof.core.planner import build_plan


def rows_of(catalog: Catalog, key: str, rows: int = 20, seed: int = 7) -> list[tuple]:
    plan = build_plan(catalog, rows=rows, seed=seed)
    generator = Generator(catalog, plan)
    table = catalog.table(key)
    return list(generator.generate_validated(table))


def test_generation_is_deterministic(catalog: Catalog) -> None:
    first = rows_of(catalog, "public.users")
    second = rows_of(catalog, "public.users")
    assert first == second


def test_foreign_keys_reference_parents(catalog: Catalog) -> None:
    accounts = rows_of(catalog, "public.accounts")
    users = rows_of(catalog, "public.users")
    parent_ids = {row[0] for row in accounts}
    for user in users:
        assert user[1] in parent_ids


def test_primary_keys_are_sequential(catalog: Catalog) -> None:
    accounts = rows_of(catalog, "public.accounts", rows=5)
    assert [row[0] for row in accounts] == [1, 2, 3, 4, 5]


def test_enum_values_are_respected(catalog: Catalog) -> None:
    users = rows_of(catalog, "public.users", rows=50)
    assert {row[3] for row in users} <= {"active", "inactive"}


def test_derived_columns_and_orderings() -> None:
    lines = Table(
        schema="public",
        name="lines",
        columns=[
            Column("id", "integer", nullable=False),
            Column("quantity", "integer", nullable=False),
            Column("unit_price", "numeric", nullable=False),
            Column("amount", "numeric", nullable=False),
            Column("starts_at", "timestamp", nullable=False),
            Column("ends_at", "timestamp", nullable=True),
        ],
        primary_key=["id"],
        checks=[
            CheckConstraint("q", "CHECK ((quantity > 0))", "(quantity > 0)"),
            CheckConstraint(
                "derived",
                "CHECK ((amount = quantity * unit_price))",
                "(amount = quantity * unit_price)",
            ),
            CheckConstraint(
                "ordered", "CHECK ((ends_at > starts_at))", "(ends_at > starts_at)"
            ),
        ],
    )
    catalog = Catalog(database="x", server_version="16", tables=[lines])
    rows = rows_of(catalog, "public.lines", rows=30)
    for row in rows:
        quantity, unit_price, amount, starts_at, ends_at = row[1], row[2], row[3], row[4], row[5]
        assert amount == Decimal(quantity) * unit_price
        if ends_at is not None:
            assert ends_at > starts_at
