from __future__ import annotations

import os

import pytest

from pgproof.adapters.script import SqlFileWriter
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


def _create_rich_schema(connection) -> None:
    with _cursor(connection) as cursor:
        cursor.execute(
            """
            SET QUOTED_IDENTIFIER ON;
            SET ANSI_NULLS ON;
            IF SCHEMA_ID('pgproof_rich') IS NULL
                EXEC('CREATE SCHEMA pgproof_rich');
            IF OBJECT_ID('pgproof_rich.order_lines','U') IS NOT NULL
                DROP TABLE pgproof_rich.order_lines;
            IF OBJECT_ID('pgproof_rich.orders','U') IS NOT NULL
                DROP TABLE pgproof_rich.orders;
            IF OBJECT_ID('pgproof_rich.ticket_seq','SO') IS NOT NULL
                DROP SEQUENCE pgproof_rich.ticket_seq;
            CREATE TABLE pgproof_rich.orders (
                id int IDENTITY(1,1) NOT NULL
                    CONSTRAINT rich_orders_pk PRIMARY KEY,
                code uniqueidentifier NOT NULL
                    CONSTRAINT rich_orders_code DEFAULT NEWID(),
                customer nvarchar(100) NOT NULL,
                total decimal(12,2) NOT NULL
                    CONSTRAINT rich_orders_total CHECK (total >= 0),
                active bit NOT NULL
                    CONSTRAINT rich_orders_active DEFAULT 1,
                created_at datetime2(3) NOT NULL
                    CONSTRAINT rich_orders_created DEFAULT SYSUTCDATETIME(),
                payload varbinary(max) NULL,
                doubled AS (total * 2) PERSISTED,
                version rowversion,
                CONSTRAINT rich_orders_customer UNIQUE (customer)
            );
            CREATE TABLE pgproof_rich.order_lines (
                id int IDENTITY(1,1) NOT NULL
                    CONSTRAINT rich_lines_pk PRIMARY KEY,
                order_id int NOT NULL,
                amount decimal(12,2) NOT NULL
                    CONSTRAINT rich_lines_amount CHECK (amount > 0),
                note nvarchar(50) NULL,
                CONSTRAINT rich_lines_order_fk FOREIGN KEY (order_id)
                    REFERENCES pgproof_rich.orders (id)
            );
            CREATE SEQUENCE pgproof_rich.ticket_seq START WITH 100 INCREMENT BY 1;
            """
        )
        connection.commit()


def test_sqlserver_rich_types_and_script_export(tmp_path) -> None:
    assert MSSQL_DSN
    connection = connect(MSSQL_DSN)
    try:
        _create_rich_schema(connection)
        catalog = filter_catalog(introspect(connection), schemas=["pgproof_rich"])
        table = catalog.table("pgproof_rich.orders")
        assert table.column("doubled").generated == "computed"
        assert table.column("version").generated == "rowversion"
        assert table.column("id").identity == "a"
    finally:
        connection.close()

    data_file = tmp_path / "data.sql"
    writer = SqlFileWriter(data_file, SqlServerDialect(), truncate=True)
    try:
        GenerateDataset(catalog, writer, rows=5, seed=11).execute()
    finally:
        writer.close()

    connection = connect(MSSQL_DSN)
    try:
        with _cursor(connection) as cursor:
            cursor.execute(data_file.read_text(encoding="utf-8"))
        connection.commit()
        report = VerifyDataset(
            catalog,
            SqlServerQueryRunner(connection),
            YamlSpecLoader(),
            SqlServerDialect(),
            seed=11,
        ).execute(VerifyRequest(dataset_hash="n/a"))
    finally:
        connection.close()

    assert report.verdict == "PASS"
