from __future__ import annotations

import os
import subprocess
from datetime import date
from pathlib import Path

import psycopg
import pytest

from pgproof.adapters.postgres.dump import DumpLoader, load_dump, split_statements
from pgproof.adapters.postgres.introspect import connect, introspect
from pgproof.adapters.postgres.query_runner import PostgresQueryRunner
from pgproof.adapters.postgres.writer import PostgresRowWriter
from pgproof.adapters.spec.yaml_loader import YamlSpecLoader
from pgproof.application.generate import GenerateDataset
from pgproof.application.verify import VerifyDataset, VerifyRequest
from pgproof.core.filters import filter_catalog

DSN = os.environ.get("PGPROOF_TEST_DSN")
ROOT = Path(__file__).resolve().parents[1]


def test_split_statements_handles_dollar_quotes_and_copy() -> None:
    sql = """
-- a comment
CREATE TABLE t (id int);
CREATE FUNCTION f() RETURNS trigger AS $$
BEGIN
  -- a semicolon; inside the body
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
COPY t (id) FROM stdin;
1
2
\\.
ALTER TABLE t ADD CONSTRAINT t_pkey PRIMARY KEY (id);
"""
    statements = split_statements(sql)
    assert len(statements) == 4
    assert statements[0].startswith("CREATE TABLE")
    assert statements[1].startswith("CREATE FUNCTION")
    assert statements[2].startswith("COPY")
    assert statements[3].startswith("ALTER TABLE")


def test_billing_dump_structure() -> None:
    catalog = load_dump(ROOT / "examples/saas-billing/schema.sql")

    assert len(catalog.tables) == 10
    assert len(catalog.functions) == 1

    users = catalog.table("billing.users")
    assert users.primary_key == ["id"]
    assert users.foreign_keys[0].parent_table == "accounts"
    assert users.uniques[0].columns == ["email"]

    accounts = catalog.table("billing.accounts")
    assert accounts.triggers[0].function == "touch_updated_at"
    assert accounts.foreign_keys[0].parent_table == "accounts"

    plans = catalog.table("billing.plans")
    assert plans.column("tier").enum_values == ["free", "pro", "business"]

    subscriptions = catalog.table("billing.subscriptions")
    assert subscriptions.column("status").enum_values == [
        "trialing",
        "active",
        "past_due",
        "canceled",
    ]


def test_partitioned_dump_tree() -> None:
    catalog = load_dump(ROOT / "examples/partitioned-events/schema.sql")

    logs = catalog.table("part.logs")
    assert logs.is_partitioned
    assert logs.has_sub_partitions
    assert len(logs.partitions) == 2
    assert logs.partitions[0].children
    assert logs.partitions[0].lower == [date(2025, 1, 1)]

    tenant_events = catalog.table("part.tenant_events")
    assert tenant_events.partition_key == ["tenant_id", "created_at"]
    assert tenant_events.partitions[0].lower == [1, None]
    assert tenant_events.partitions[1].upper == [200, None]


def test_inferred_foreign_key_from_naming() -> None:
    dump = """
    CREATE TABLE customers (id integer PRIMARY KEY);
    CREATE TABLE orders (
        id integer PRIMARY KEY,
        customer_id integer NOT NULL
    );
    """
    catalog = DumpLoader().load(dump)
    orders = catalog.table("public.orders")

    assert len(orders.foreign_keys) == 1
    foreign_key = orders.foreign_keys[0]
    assert foreign_key.inferred is True
    assert foreign_key.parent_table == "customers"
    assert foreign_key.child_columns == ["customer_id"]


@pytest.mark.integration
@pytest.mark.skipif(not DSN, reason="PGPROOF_TEST_DSN is not set")
def test_dump_matches_live_introspection() -> None:
    assert DSN
    subprocess.run(
        [
            "psql",
            DSN,
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            str(ROOT / "examples/saas-billing/schema.sql"),
        ],
        check=True,
        capture_output=True,
    )
    dump = load_dump(ROOT / "examples/saas-billing/schema.sql")
    with connect(DSN) as conn:
        live = filter_catalog(introspect(conn), schemas=["billing"])

    dump_tables = {table.key: table for table in dump.tables}
    live_tables = {table.key: table for table in live.tables}
    assert set(dump_tables) == set(live_tables)

    for key, dump_table in dump_tables.items():
        live_table = live_tables[key]
        assert [column.name for column in dump_table.columns] == [
            column.name for column in live_table.columns
        ]
        assert dump_table.primary_key == live_table.primary_key
        assert len(dump_table.foreign_keys) == len(live_table.foreign_keys)
        assert len(dump_table.checks) == len(live_table.checks)
        assert len(dump_table.uniques) == len(live_table.uniques)
        for column in dump_table.columns:
            live_column = live_table.column(column.name)
            assert column.nullable == live_column.nullable
            assert column.data_kind == live_column.data_kind
            assert column.enum_values == live_column.enum_values
        assert {trigger.function for trigger in dump_table.triggers} == {
            trigger.function for trigger in live_table.triggers
        }


@pytest.mark.integration
@pytest.mark.skipif(not DSN, reason="PGPROOF_TEST_DSN is not set")
def test_dump_only_generation_passes() -> None:
    assert DSN
    subprocess.run(
        [
            "psql",
            DSN,
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            str(ROOT / "examples/saas-billing/schema.sql"),
        ],
        check=True,
        capture_output=True,
    )
    catalog = load_dump(ROOT / "examples/saas-billing/schema.sql")

    connection = psycopg.connect(DSN)
    writer = PostgresRowWriter(connection)
    try:
        generated = GenerateDataset(catalog, writer, rows=25, seed=11).execute()
    finally:
        writer.close()

    connection = psycopg.connect(DSN)
    try:
        verify = VerifyDataset(
            catalog, PostgresQueryRunner(connection), YamlSpecLoader(), seed=11
        )
        report = verify.execute(
            VerifyRequest(
                spec_path=str(ROOT / "examples/saas-billing/plan.yml"),
                dataset_hash=generated.dataset_hash,
                plan=generated.plan,
            )
        )
    finally:
        connection.close()

    assert report.verdict == "PASS"
