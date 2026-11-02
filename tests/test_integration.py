"""End-to-end test against a real PostgreSQL.

Run with:

    PGPROOF_TEST_DSN=postgresql:///pgproof_demo pytest -m integration

The target schema must be the one in examples/saas-billing/schema.sql.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from pgproof.adapters.postgres.introspect import connect, introspect, table_counts
from pgproof.adapters.postgres.query_runner import PostgresQueryRunner
from pgproof.adapters.postgres.writer import reset_sequences, truncate, write_table
from pgproof.core.generator import Generator
from pgproof.core.invariants import invariants_from_catalog
from pgproof.core.planner import build_plan
from pgproof.core.verifier import verify_all

pytestmark = pytest.mark.integration

DSN = os.environ.get("PGPROOF_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="PGPROOF_TEST_DSN is not set")
def test_end_to_end_passes_every_invariant() -> None:
    assert DSN
    with connect(DSN) as source:
        catalog = introspect(source)
    assert catalog.tables, "load examples/saas-billing/schema.sql first"

    plan = build_plan(catalog, rows=25, seed=99)
    generator = Generator(catalog, plan)

    connection = psycopg.connect(DSN)
    try:
        truncate(connection, catalog.tables, cascade=True)
        for key in plan.order:
            write_table(connection, catalog.table(key), generator.generate_validated(catalog.table(key)))
        reset_sequences(connection, catalog.tables)
        connection.commit()

        verification = verify_all(
            PostgresQueryRunner(connection), invariants_from_catalog(catalog)
        )
        counts = table_counts(connection, catalog.table_keys)
    finally:
        connection.close()

    failed = [result for result in verification.results if result.status in {"fail", "error"}]
    assert not failed, [(result.id, result.error) for result in failed]
    assert sum(counts.values()) == plan.total_rows


@pytest.mark.skipif(not DSN, reason="PGPROOF_TEST_DSN is not set")
def test_same_seed_same_rows() -> None:
    assert DSN
    with connect(DSN) as source:
        catalog = introspect(source)
    plan = build_plan(catalog, rows=10, seed=5)
    first = {
        key: list(Generator(catalog, plan).generate_validated(catalog.table(key)))
        for key in plan.order
    }
    second = {
        key: list(Generator(catalog, build_plan(catalog, rows=10, seed=5)).generate_validated(catalog.table(key)))
        for key in plan.order
    }
    assert first == second
