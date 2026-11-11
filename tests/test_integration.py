"""End-to-end test against a real PostgreSQL.

Run with:

    PGPROOF_TEST_DSN=postgresql:///pgproof_demo pytest -m integration

The target schema must be the one in examples/saas-billing/schema.sql.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from pgproof.adapters.postgres.dialect import PostgresDialect
from pgproof.adapters.postgres.introspect import connect, introspect, table_counts
from pgproof.adapters.postgres.query_runner import PostgresQueryRunner
from pgproof.adapters.postgres.writer import (
    PostgresRowWriter,
    reset_sequences,
    truncate,
    write_table,
)
from pgproof.adapters.spec.yaml_loader import YamlSpecLoader
from pgproof.application.generate import GenerateDataset
from pgproof.application.verify import VerifyDataset, VerifyRequest
from pgproof.core.filters import filter_catalog
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
            PostgresQueryRunner(connection), invariants_from_catalog(catalog, PostgresDialect())
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


@pytest.mark.skipif(not DSN, reason="PGPROOF_TEST_DSN is not set")
def test_partial_unique_index_is_generated_and_verified() -> None:
    assert DSN
    connection = psycopg.connect(DSN)
    try:
        connection.execute("DROP TABLE IF EXISTS public.pgproof_partial")
        connection.execute(
            """
            CREATE TABLE public.pgproof_partial (
                id integer PRIMARY KEY,
                seat integer NOT NULL CHECK (seat BETWEEN 1 AND 20)
            )
            """
        )
        connection.execute(
            "CREATE UNIQUE INDEX uq_partial_seat "
            "ON public.pgproof_partial (seat) WHERE seat IS NOT NULL"
        )
        connection.execute(
            "CREATE UNIQUE INDEX uq_partial_expr "
            "ON public.pgproof_partial ((seat + 0)) WHERE seat IS NOT NULL"
        )
        connection.commit()
        catalog = filter_catalog(introspect(connection), include=["pgproof_partial"])
    finally:
        connection.close()

    table = catalog.table("public.pgproof_partial")
    seat_index = next(item for item in table.uniques if item.name == "uq_partial_seat")
    assert seat_index.columns == ["seat"]
    assert seat_index.predicate is not None
    assert any("expression" in warning for warning in catalog.warnings)

    connection = psycopg.connect(DSN)
    writer = PostgresRowWriter(connection)
    try:
        generated = GenerateDataset(catalog, writer, rows=20, seed=11).execute()
    finally:
        writer.close()
        connection.close()

    connection = psycopg.connect(DSN)
    try:
        report = VerifyDataset(
            catalog,
            PostgresQueryRunner(connection),
            YamlSpecLoader(),
            PostgresDialect(),
            seed=11,
        ).execute(
            VerifyRequest(dataset_hash=generated.dataset_hash, plan=generated.plan)
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(DISTINCT seat) FROM public.pgproof_partial")
            distinct = int(cursor.fetchone()[0])
        connection.execute("DROP TABLE public.pgproof_partial")
        connection.commit()
    finally:
        connection.close()

    assert distinct == 20
    assert report.verdict == "PASS"
    assert any("expression" in warning for warning in report.warnings)
