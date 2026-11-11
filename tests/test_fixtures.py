"""Every fixture schema must generate and verify cleanly.

Run with:

    PGPROOF_TEST_DSN=postgresql:///pgproof_demo pytest -m integration
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from pgproof.adapters.postgres.dialect import PostgresDialect
from pgproof.adapters.postgres.introspect import connect, introspect
from pgproof.adapters.postgres.query_runner import PostgresQueryRunner
from pgproof.adapters.postgres.writer import PostgresRowWriter
from pgproof.adapters.spec.yaml_loader import YamlSpecLoader
from pgproof.application.generate import GenerateDataset
from pgproof.application.verify import VerifyDataset, VerifyRequest

pytestmark = pytest.mark.integration

DSN = os.environ.get("PGPROOF_TEST_DSN")
ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ["ecommerce", "multi-tenant-crm", "analytics", "partitioned-events"]


def _psql(*args: str) -> None:
    assert DSN
    subprocess.run(
        ["psql", DSN, "-q", "-v", "ON_ERROR_STOP=1", *args],
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(not DSN, reason="PGPROOF_TEST_DSN is not set")
@pytest.mark.parametrize("fixture", FIXTURES)
def test_fixture_generates_and_verifies(fixture: str) -> None:
    assert DSN
    _psql(
        "-c",
        "DROP SCHEMA IF EXISTS shop CASCADE;"
        "DROP SCHEMA IF EXISTS crm CASCADE;"
        "DROP SCHEMA IF EXISTS analytics CASCADE;"
        "DROP SCHEMA IF EXISTS part CASCADE;",
    )
    _psql("-f", str(ROOT / "examples" / fixture / "schema.sql"))

    with connect(DSN) as conn:
        catalog = introspect(conn)
    assert catalog.tables

    connection = psycopg.connect(DSN)
    writer = PostgresRowWriter(connection)
    try:
        generated = GenerateDataset(catalog, writer, rows=25, seed=11).execute()
    finally:
        writer.close()

    connection = psycopg.connect(DSN)
    try:
        verify = VerifyDataset(
            catalog, PostgresQueryRunner(connection), YamlSpecLoader(), PostgresDialect(), seed=11
        )
        report = verify.execute(
            VerifyRequest(
                spec_path=str(ROOT / "examples" / fixture / "plan.yml"),
                dataset_hash=generated.dataset_hash,
                plan=generated.plan,
            )
        )
    finally:
        connection.close()

    failures = [result for result in report.results if result.status in {"fail", "error"}]
    assert report.verdict == "PASS", [
        (result.id, result.violations, result.error) for result in failures
    ]
