from __future__ import annotations

import os

import pytest

from pgproof.adapters.spec.yaml_loader import YamlSpecLoader
from pgproof.adapters.sqlserver.dialect import SqlServerDialect
from pgproof.adapters.sqlserver.introspect import _cursor, connect, introspect
from pgproof.adapters.sqlserver.query_runner import SqlServerQueryRunner
from pgproof.adapters.sqlserver.writer import SqlServerRowWriter
from pgproof.application.generate import GenerateDataset
from pgproof.application.verify import VerifyDataset, VerifyRequest
from pgproof.core.filters import filter_catalog

MSSQL_DSN = os.environ.get("PGPROOF_MSSQL_DSN")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not MSSQL_DSN, reason="PGPROOF_MSSQL_DSN is not set"),
]

SCHEMA = "pgproof_test"


def _create_schema(connection) -> None:
    with _cursor(connection) as cursor:
        cursor.execute(
            f"""
            IF SCHEMA_ID('{SCHEMA}') IS NULL EXEC('CREATE SCHEMA {SCHEMA}');
            IF OBJECT_ID('{SCHEMA}.children', 'U') IS NOT NULL
                DROP TABLE {SCHEMA}.children;
            IF OBJECT_ID('{SCHEMA}.parents', 'U') IS NOT NULL
                DROP TABLE {SCHEMA}.parents;
            CREATE TABLE {SCHEMA}.parents (
                id int NOT NULL CONSTRAINT parents_pk PRIMARY KEY,
                name nvarchar(50) NOT NULL
            );
            CREATE TABLE {SCHEMA}.children (
                id int NOT NULL CONSTRAINT children_pk PRIMARY KEY,
                parent_id int NOT NULL,
                amount decimal(12, 2) NOT NULL
                    CONSTRAINT children_amount_check CHECK (amount >= 0),
                CONSTRAINT children_parent_fk FOREIGN KEY (parent_id)
                    REFERENCES {SCHEMA}.parents (id)
            );
            INSERT INTO {SCHEMA}.parents (id, name) VALUES (1, N'padre');
            INSERT INTO {SCHEMA}.children (id, parent_id, amount)
                VALUES (1, 1, 10.00);
            """
        )
        connection.commit()


def test_sqlserver_source_verification() -> None:
    assert MSSQL_DSN
    connection = connect(MSSQL_DSN)
    try:
        _create_schema(connection)
        catalog = filter_catalog(introspect(connection), schemas=[SCHEMA])
        assert catalog.table(f"{SCHEMA}.parents").primary_key == ["id"]
        assert catalog.table(f"{SCHEMA}.children").foreign_keys
        assert any(
            check.name == "children_amount_check"
            for check in catalog.table(f"{SCHEMA}.children").checks
        )

        report = VerifyDataset(
            catalog,
            SqlServerQueryRunner(connection),
            YamlSpecLoader(),
            SqlServerDialect(),
        ).execute(VerifyRequest(dataset_hash="n/a"))
    finally:
        connection.close()

    assert report.verdict == "PASS"


def test_sqlserver_generation_and_verification() -> None:
    assert MSSQL_DSN
    connection = connect(MSSQL_DSN)
    try:
        _create_schema(connection)
        catalog = filter_catalog(introspect(connection), schemas=[SCHEMA])
        writer = SqlServerRowWriter(connection)
        try:
            generated = GenerateDataset(catalog, writer, rows=10, seed=7).execute()
        finally:
            writer.close()
    finally:
        connection.close()

    verify_connection = connect(MSSQL_DSN)
    try:
        report = VerifyDataset(
            catalog,
            SqlServerQueryRunner(verify_connection),
            YamlSpecLoader(),
            SqlServerDialect(),
            seed=7,
        ).execute(
            VerifyRequest(dataset_hash=generated.dataset_hash, plan=generated.plan)
        )
    finally:
        verify_connection.close()

    assert report.verdict == "PASS"
