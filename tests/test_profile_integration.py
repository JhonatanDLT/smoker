"""Profile-aware generation against a live PostgreSQL."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from pgproof.adapters.postgres.introspect import connect, introspect
from pgproof.adapters.postgres.profile import profile_catalog
from pgproof.adapters.postgres.query_runner import PostgresQueryRunner
from pgproof.adapters.postgres.writer import PostgresRowWriter
from pgproof.adapters.spec.yaml_loader import YamlSpecLoader
from pgproof.application.generate import GenerateDataset
from pgproof.application.verify import VerifyDataset, VerifyRequest

pytestmark = pytest.mark.integration

DSN = os.environ.get("PGPROOF_TEST_DSN")
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not DSN, reason="PGPROOF_TEST_DSN is not set")
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
            catalog, PostgresQueryRunner(connection), YamlSpecLoader(), seed=7
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
