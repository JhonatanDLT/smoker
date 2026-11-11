from __future__ import annotations

from decimal import Decimal

from smoker.core.generator import Generator
from smoker.core.ir import (
    Catalog,
    CheckConstraint,
    Column,
    ForeignKey,
    Table,
    UniqueConstraint,
)
from smoker.core.planner import build_plan


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


def test_filtered_unique_index_is_scoped_to_its_predicate() -> None:
    from smoker.adapters.sqlserver.dump import SqlServerDumpLoader
    from smoker.core.generator import Generator
    from smoker.core.planner import build_plan

    catalog = SqlServerDumpLoader().load(
        """
        CREATE TABLE [dbo].[users] (
            [id] int IDENTITY(1,1) PRIMARY KEY,
            [seat] int NOT NULL,
            [active] bit NOT NULL
        );
        GO
        CREATE UNIQUE INDEX [uq_active_seat]
            ON [dbo].[users]([seat]) WHERE [active] = 1;
        """
    )
    table = catalog.table("dbo.users")
    assert table.uniques[0].predicate is not None
    generator = Generator(catalog, build_plan(catalog, rows=30, seed=17))
    rows = list(generator.generate_validated(table))
    active_seats = [row[1] for row in rows if row[2]]

    assert len(set(active_seats)) == len(active_seats)


def test_composite_key_with_fk_reuses_parent_identifiers() -> None:
    parents = Table(
        schema="public",
        name="parents",
        columns=[Column("id", "uuid", nullable=False)],
        primary_key=["id"],
    )
    events = Table(
        schema="public",
        name="events",
        columns=[
            Column("parent_id", "uuid", nullable=False),
            Column("sequence", "integer", nullable=False),
        ],
        primary_key=["parent_id", "sequence"],
        foreign_keys=[
            ForeignKey(
                name="events_parent_fk",
                child_schema="public",
                child_table="events",
                child_columns=["parent_id"],
                parent_schema="public",
                parent_table="parents",
                parent_columns=["id"],
            )
        ],
    )
    catalog = Catalog(
        database="db", server_version="16", tables=[parents, events]
    )

    parent_rows = rows_of(catalog, "public.parents", rows=12)
    event_rows = rows_of(catalog, "public.events", rows=12)
    parent_ids = {row[0] for row in parent_rows}

    assert all(row[0] in parent_ids for row in event_rows)
    assert len({row[0] for row in event_rows}) > 1


def test_partial_unique_moves_row_out_of_predicate_when_key_is_immutable() -> None:
    accounts = Table(
        schema="public",
        name="accounts",
        columns=[Column("id", "uuid", nullable=False)],
        primary_key=["id"],
    )
    subscriptions = Table(
        schema="public",
        name="subscriptions",
        columns=[
            Column("id", "uuid", nullable=False),
            Column("account_id", "uuid", nullable=False),
            Column(
                "status",
                "subscription_status",
                nullable=False,
                enum_values=["trialing", "active", "past_due", "cancelled"],
            ),
        ],
        primary_key=["id"],
        foreign_keys=[
            ForeignKey(
                name="subscriptions_account_fk",
                child_schema="public",
                child_table="subscriptions",
                child_columns=["account_id"],
                parent_schema="public",
                parent_table="accounts",
                parent_columns=["id"],
            )
        ],
        uniques=[
            UniqueConstraint(
                name="subscriptions_one_active",
                columns=["account_id"],
                predicate="status = 'active'::billing.subscription_status",
            )
        ],
    )
    catalog = Catalog(
        database="db", server_version="16", tables=[accounts, subscriptions]
    )

    parent_rows = rows_of(catalog, "public.accounts", rows=20)
    rows = rows_of(catalog, "public.subscriptions", rows=20)
    parent_ids = {row[0] for row in parent_rows}
    active_per_account: dict[object, int] = {}

    for row in rows:
        assert row[1] in parent_ids
        if row[2] == "active":
            active_per_account[row[1]] = active_per_account.get(row[1], 0) + 1

    assert all(count <= 1 for count in active_per_account.values())


def test_unique_foreign_key_uses_each_parent_once() -> None:
    orders = Table(
        schema="public",
        name="orders",
        columns=[Column("id", "integer", nullable=False)],
        primary_key=["id"],
    )
    payments = Table(
        schema="public",
        name="payments",
        columns=[
            Column("id", "integer", nullable=False),
            Column("order_id", "integer", nullable=False),
        ],
        primary_key=["id"],
        foreign_keys=[
            ForeignKey(
                name="payments_order_fk",
                child_schema="public",
                child_table="payments",
                child_columns=["order_id"],
                parent_schema="public",
                parent_table="orders",
                parent_columns=["id"],
            )
        ],
        uniques=[
            UniqueConstraint(name="payments_order_key", columns=["order_id"])
        ],
    )
    catalog = Catalog(
        database="db", server_version="16", tables=[orders, payments]
    )

    rows = rows_of(catalog, "public.payments", rows=10)

    assert sorted(row[1] for row in rows) == list(range(1, 11))
