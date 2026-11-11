"""TABLESAMPLE and date histograms against a live PostgreSQL."""

from __future__ import annotations

import os
import subprocess
from datetime import date
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
def test_system_sampling_and_date_histograms() -> None:
    assert DSN
    subprocess.run(
        [
            "psql",
            DSN,
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            (
                "DROP SCHEMA IF EXISTS sample CASCADE;"
                "CREATE SCHEMA sample;"
                "CREATE TABLE sample.big ("
                "  id integer PRIMARY KEY,"
                "  created_at date NOT NULL,"
                "  amount numeric(12,2) NOT NULL CHECK (amount >= 0)"
                ");"
                "INSERT INTO sample.big "
                "SELECT i, date '2025-01-01' + (i % 365), (i % 10000)::numeric "
                "FROM generate_series(1, 60000) AS i;"
                "ANALYZE sample.big;"
            ),
        ],
        check=True,
        capture_output=True,
    )

    with connect(DSN) as conn:
        catalog = introspect(conn)
    table = catalog.table("sample.big")

    connection = psycopg.connect(DSN)
    try:
        profile = profile_catalog(
            connection, [table], sample=500, method="system", seed=7
        )
    finally:
        connection.close()

    stats = profile.tables["sample.big"]
    assert stats.sample_method == "system"
    assert stats.sample_pct is not None
    assert 50 <= stats.columns["id"].sampled <= 500
    assert stats.columns["created_at"].bucket_kind == "date"
    assert stats.columns["created_at"].buckets

    connection = psycopg.connect(DSN)
    writer = PostgresRowWriter(connection)
    try:
        generated = GenerateDataset(
            catalog, writer, rows=300, seed=7, profile=profile
        ).execute()
    finally:
        writer.close()

    connection = psycopg.connect(DSN)
    try:
        verify = VerifyDataset(
            catalog, PostgresQueryRunner(connection), YamlSpecLoader(), PostgresDialect(), seed=7
        )
        report = verify.execute(
            VerifyRequest(dataset_hash=generated.dataset_hash, plan=generated.plan)
        )
        rows = connection.execute(
            "SELECT min(created_at), max(created_at) FROM sample.big"
        ).fetchone()
    finally:
        connection.close()

    low, high = rows
    assert date(2025, 1, 1) <= low <= high <= date(2026, 1, 1)
    assert report.verdict == "PASS"
