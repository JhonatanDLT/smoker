"""Profile-aware generation against a live PostgreSQL."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from smoker.adapters.postgres.dialect import PostgresDialect
from smoker.adapters.postgres.introspect import connect, introspect
from smoker.adapters.postgres.profile import profile_catalog
from smoker.adapters.postgres.query_runner import PostgresQueryRunner
from smoker.adapters.postgres.writer import PostgresRowWriter
from smoker.adapters.spec.yaml_loader import YamlSpecLoader
from smoker.application.generate import GenerateDataset
from smoker.application.verify import VerifyDataset, VerifyRequest

pytestmark = pytest.mark.integration

DSN = os.environ.get("SMOKER_TEST_DSN")
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not DSN, reason="SMOKER_TEST_DSN is not set")
def test_profile_reproduces_the_source_skew() -> None:
    assert DSN
    subprocess.run(
        ["psql", DSN, "-q", "-v", "ON_ERROR_STOP=1", "-f", str(ROOT / "examples/saas-billing/schema.sql")],
        check=True,
        capture_output=True,
    )
    with connect(DSN) as conn:
        catalog = introspect(conn)

    connection = psycopg.connect(DSN)
    writer = PostgresRowWriter(connection)
    GenerateDataset(catalog, writer, rows=100, seed=1).execute()
    writer.close()

    connection = psycopg.connect(DSN)
    try:
        connection.execute(
            "UPDATE billing.invoices "
            "SET status = CASE WHEN id % 5 = 0 "
            "THEN 'paid'::billing.invoice_status ELSE 'open'::billing.invoice_status END"
        )
        connection.commit()
        profile = profile_catalog(connection, catalog.tables, sample=200)
    finally:
        connection.close()

    connection = psycopg.connect(DSN)
    writer = PostgresRowWriter(connection)
    GenerateDataset(catalog, writer, rows=200, seed=7, profile=profile).execute()
    writer.close()

    connection = psycopg.connect(DSN)
    try:
        verify = VerifyDataset(
            catalog, PostgresQueryRunner(connection), YamlSpecLoader(), PostgresDialect(), seed=7
        )
        report = verify.execute(
            VerifyRequest(spec_path=str(ROOT / "examples/saas-billing/plan.yml"))
        )
        rows = connection.execute(
            "SELECT status::text, count(*) FROM billing.invoices GROUP BY 1"
        ).fetchall()
    finally:
        connection.close()

    counts = dict(rows)
    total = sum(counts.values())
    assert abs(counts["open"] / total - 0.80) < 0.08
    assert report.verdict == "PASS"
