from __future__ import annotations

import os
from pathlib import Path

import pytest

from pgproof.adapters.script import SqlFileWriter
from pgproof.adapters.spec.yaml_loader import YamlSpecLoader
from pgproof.adapters.sqlserver.dialect import SqlServerDialect
from pgproof.adapters.sqlserver.introspect import _cursor, connect, introspect
from pgproof.adapters.sqlserver.profile import profile_catalog
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

    connection = connect(MSSQL_DSN)
    try:
        profile = profile_catalog(
            connection, catalog.tables, sample=5, method="limit", seed=11
        )
    finally:
        connection.close()
    assert profile.table("pgproof_rich.orders") is not None
    assert profile.column("pgproof_rich.orders", "total") is not None


DUMP = """
IF OBJECT_ID('pgproof_dump.v_children', 'V') IS NOT NULL DROP VIEW [pgproof_dump].[v_children];
IF OBJECT_ID('pgproof_dump.children_guard', 'TR') IS NOT NULL DROP TRIGGER [pgproof_dump].[children_guard];
IF OBJECT_ID('pgproof_dump.p_children', 'P') IS NOT NULL DROP PROCEDURE [pgproof_dump].[p_children];
IF OBJECT_ID('pgproof_dump.children', 'U') IS NOT NULL DROP TABLE [pgproof_dump].[children];
IF OBJECT_ID('pgproof_dump.parents', 'U') IS NOT NULL DROP TABLE [pgproof_dump].[parents];
IF SCHEMA_ID('pgproof_dump') IS NOT NULL EXEC('DROP SCHEMA pgproof_dump');
GO
CREATE SCHEMA [pgproof_dump];
GO
CREATE TABLE [pgproof_dump].[parents] (
    [id] int IDENTITY(1,1) NOT NULL CONSTRAINT [parents_pk] PRIMARY KEY,
    [name] nvarchar(80) NOT NULL,
    [active] bit NOT NULL DEFAULT 1
);
GO
CREATE TABLE [pgproof_dump].[children] (
    [id] int IDENTITY(1,1) NOT NULL CONSTRAINT [children_pk] PRIMARY KEY,
    [parent_id] int NOT NULL,
    [amount] decimal(12,2) NOT NULL
        CONSTRAINT [children_amount] CHECK ([amount] >= 0),
    CONSTRAINT [children_parent_fk] FOREIGN KEY ([parent_id])
        REFERENCES [pgproof_dump].[parents]([id])
);
GO
CREATE VIEW [pgproof_dump].[v_children] AS
    SELECT [id], [parent_id], [amount] FROM [pgproof_dump].[children];
GO
CREATE PROCEDURE [pgproof_dump].[p_children] @min decimal(12,2) AS
BEGIN
    IF @min < 0 THROW 50001, 'negative', 1;
    SELECT [id] FROM [pgproof_dump].[children] WHERE [amount] >= @min;
END;
GO
CREATE TRIGGER [pgproof_dump].[children_guard]
ON [pgproof_dump].[children] AFTER INSERT AS
BEGIN
    IF EXISTS (SELECT 1 FROM inserted WHERE [amount] < 0)
        THROW 50002, 'bad amount', 1;
END;
GO
"""


def test_dump_loading_applies_to_sqlserver_and_generates(tmp_path) -> None:
    assert MSSQL_DSN
    dump_file = tmp_path / "schema.sql"
    dump_file.write_text(DUMP, encoding="utf-8")

    from pgproof.adapters.sqlserver.dump import SqlServerDumpLoader, split_batches

    catalog = SqlServerDumpLoader().load(DUMP)
    assert catalog.table("pgproof_dump.children").foreign_keys
    routine = next(
        item for item in catalog.functions if item.kind == "procedure"
    )
    assert routine.name == "p_children"
    assert "THROW" in routine.body

    connection = connect(MSSQL_DSN)
    try:
        with _cursor(connection) as cursor:
            cursor.execute("SET QUOTED_IDENTIFIER ON; SET ANSI_NULLS ON;")
            for batch in split_batches(DUMP):
                cursor.execute(batch)
        connection.commit()
    finally:
        connection.close()

    live_connection = connect(MSSQL_DSN)
    try:
        live = filter_catalog(introspect(live_connection), schemas=["pgproof_dump"])
    finally:
        live_connection.close()
    live_routine = next(
        item for item in live.functions if item.name == "p_children"
    )
    assert live_routine.kind == "procedure"
    assert any(
        rule.expression == "@min >= 0" and rule.provenance.kind == "function"
        for rule in live.rules
    )

    connection = connect(MSSQL_DSN)
    writer = SqlServerRowWriter(connection)
    try:
        generated = GenerateDataset(catalog, writer, rows=5, seed=3).execute()
    finally:
        writer.close()

    verify_connection = connect(MSSQL_DSN)
    try:
        report = VerifyDataset(
            catalog,
            SqlServerQueryRunner(verify_connection),
            YamlSpecLoader(),
            SqlServerDialect(),
            seed=3,
        ).execute(
            VerifyRequest(dataset_hash=generated.dataset_hash, plan=generated.plan)
        )
    finally:
        verify_connection.close()

    assert report.verdict == "PASS"


def test_sqlserver_range_partition_introspection_and_generation() -> None:
    assert MSSQL_DSN
    connection = connect(MSSQL_DSN)
    try:
        with _cursor(connection) as cursor:
            cursor.execute(
                """
                SET QUOTED_IDENTIFIER ON;
                IF OBJECT_ID('pgproof_parts.events','U') IS NOT NULL
                    DROP TABLE pgproof_parts.events;
                IF EXISTS (SELECT 1 FROM sys.partition_schemes WHERE name='ps_events')
                    DROP PARTITION SCHEME ps_events;
                IF EXISTS (SELECT 1 FROM sys.partition_functions WHERE name='pf_events')
                    DROP PARTITION FUNCTION pf_events;
                IF SCHEMA_ID('pgproof_parts') IS NULL
                    EXEC('CREATE SCHEMA pgproof_parts');
                CREATE PARTITION FUNCTION pf_events (int)
                    AS RANGE RIGHT FOR VALUES (100, 200, 300);
                CREATE PARTITION SCHEME ps_events
                    AS PARTITION pf_events ALL TO ([PRIMARY]);
                CREATE TABLE pgproof_parts.events (
                    id int IDENTITY(1,1) NOT NULL,
                    bucket int NOT NULL,
                    amount decimal(12,2) NOT NULL
                        CONSTRAINT parts_amount CHECK (amount >= 0),
                    CONSTRAINT parts_pk PRIMARY KEY (id, bucket)
                        ON ps_events(bucket)
                ) ON ps_events(bucket);
                """
            )
        connection.commit()
        catalog = filter_catalog(introspect(connection), schemas=["pgproof_parts"])
    finally:
        connection.close()

    table = catalog.table("pgproof_parts.events")
    assert table.is_partitioned is True
    assert table.partition_strategy == "range"
    assert table.partition_key == ["bucket"]
    assert table.partitions
    assert table.partitions[0].lower == [100]
    assert table.partitions[0].upper == [300]

    connection = connect(MSSQL_DSN)
    writer = SqlServerRowWriter(connection)
    try:
        generated = GenerateDataset(catalog, writer, rows=10, seed=4).execute()
    finally:
        writer.close()

    verify_connection = connect(MSSQL_DSN)
    try:
        report = VerifyDataset(
            catalog,
            SqlServerQueryRunner(verify_connection),
            YamlSpecLoader(),
            SqlServerDialect(),
            seed=4,
        ).execute(
            VerifyRequest(dataset_hash=generated.dataset_hash, plan=generated.plan)
        )
    finally:
        verify_connection.close()

    assert report.verdict == "PASS"


def test_sqlserver_filtered_unique_index() -> None:
    assert MSSQL_DSN
    connection = connect(MSSQL_DSN)
    try:
        with _cursor(connection) as cursor:
            cursor.execute(
                """
                SET QUOTED_IDENTIFIER ON;
                IF OBJECT_ID('pgproof_filtered.users','U') IS NOT NULL
                    DROP TABLE pgproof_filtered.users;
                IF SCHEMA_ID('pgproof_filtered') IS NULL
                    EXEC('CREATE SCHEMA pgproof_filtered');
                CREATE TABLE pgproof_filtered.users (
                    id int IDENTITY(1,1) NOT NULL
                        CONSTRAINT filtered_pk PRIMARY KEY,
                    seat int NOT NULL
                        CONSTRAINT filtered_seat_range
                        CHECK ([seat] >= 1 AND [seat] <= 20),
                    active bit NOT NULL
                );
                CREATE UNIQUE INDEX uq_active_seat
                    ON pgproof_filtered.users(seat) WHERE active = 1;
                """
            )
        connection.commit()
        catalog = filter_catalog(
            introspect(connection), schemas=["pgproof_filtered"]
        )
    finally:
        connection.close()

    table = catalog.table("pgproof_filtered.users")
    assert table.uniques[0].predicate is not None

    connection = connect(MSSQL_DSN)
    writer = SqlServerRowWriter(connection)
    try:
        generated = GenerateDataset(catalog, writer, rows=20, seed=3).execute()
    finally:
        writer.close()

    verify_connection = connect(MSSQL_DSN)
    try:
        runner = SqlServerQueryRunner(verify_connection)
        report = VerifyDataset(
            catalog,
            runner,
            YamlSpecLoader(),
            SqlServerDialect(),
            seed=3,
        ).execute(
            VerifyRequest(dataset_hash=generated.dataset_hash, plan=generated.plan)
        )
        with _cursor(verify_connection) as cursor:
            cursor.execute(
                "SET QUOTED_IDENTIFIER ON; "
                "INSERT INTO pgproof_filtered.users (seat, active) "
                "VALUES (1, 0), (1, 0);"
            )
        verify_connection.commit()
        second = VerifyDataset(
            catalog,
            runner,
            YamlSpecLoader(),
            SqlServerDialect(),
            seed=3,
        ).execute(VerifyRequest(dataset_hash="n/a"))
    finally:
        verify_connection.close()

    assert report.verdict == "PASS"
    assert second.verdict == "PASS"


def test_sqlserver_multi_database_introspection_and_generation() -> None:
    assert MSSQL_DSN
    connection = connect(MSSQL_DSN)
    try:
        connection.connection.autocommit = True
        with _cursor(connection) as cursor:
            for name in ("pgproof_multi_a", "pgproof_multi_b"):
                cursor.execute(
                    f"IF DB_ID('{name}') IS NOT NULL BEGIN "
                    f"ALTER DATABASE [{name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
                    f"DROP DATABASE [{name}]; END"
                )
            cursor.execute("CREATE DATABASE pgproof_multi_a")
            cursor.execute("CREATE DATABASE pgproof_multi_b")
            cursor.execute(
                "USE pgproof_multi_a; "
                "CREATE TABLE dbo.users ("
                "id int IDENTITY(1,1) NOT NULL CONSTRAINT multi_users_pk PRIMARY KEY, "
                "name nvarchar(50) NOT NULL)"
            )
            cursor.execute(
                "USE pgproof_multi_b; "
                "CREATE TABLE dbo.orders ("
                "id int IDENTITY(1,1) NOT NULL CONSTRAINT multi_orders_pk PRIMARY KEY, "
                "user_id int NOT NULL, "
                "amount decimal(10,2) NOT NULL "
                "CONSTRAINT multi_amount CHECK (amount >= 0))"
            )
        connection.connection.autocommit = False
        catalog = introspect(
            connection, databases=["pgproof_multi_a", "pgproof_multi_b"]
        )
    finally:
        connection.close()

    assert catalog.databases == ["pgproof_multi_a", "pgproof_multi_b"]
    users = catalog.table("pgproof_multi_a.dbo.users")
    orders = catalog.table("pgproof_multi_b.dbo.orders")
    assert users.database == "pgproof_multi_a"
    assert orders.database == "pgproof_multi_b"
    assert orders.foreign_keys[0].parent_key == "pgproof_multi_a.dbo.users"

    connection = connect(MSSQL_DSN)
    writer = SqlServerRowWriter(connection)
    try:
        generated = GenerateDataset(catalog, writer, rows=10, seed=5).execute()
    finally:
        writer.close()

    verify_connection = connect(MSSQL_DSN)
    try:
        report = VerifyDataset(
            catalog,
            SqlServerQueryRunner(verify_connection),
            YamlSpecLoader(),
            SqlServerDialect(),
            seed=5,
        ).execute(
            VerifyRequest(dataset_hash=generated.dataset_hash, plan=generated.plan)
        )
    finally:
        verify_connection.close()

    assert report.verdict == "PASS"


def test_azure_dialect_single_database_mode() -> None:
    assert MSSQL_DSN
    from pgproof.adapters.sqlserver.azure import AzureSqlDialect

    connection = connect(MSSQL_DSN)
    try:
        with _cursor(connection) as cursor:
            cursor.execute("SELECT DB_NAME()")
            current = str(cursor.fetchone()[0])
        catalog = introspect(
            connection, databases=[current], dialect=AzureSqlDialect()
        )
        assert catalog.databases == []
        try:
            introspect(
                connection,
                databases=[current, "tempdb"],
                dialect=AzureSqlDialect(),
            )
        except ValueError as exc:
            assert "multiple databases" in str(exc)
        else:
            raise AssertionError("Azure SQL accepted multiple databases")
    finally:
        connection.close()


def test_sqlserver_temporal_history_population() -> None:
    assert MSSQL_DSN
    connection = connect(MSSQL_DSN)
    try:
        with _cursor(connection) as cursor:
            cursor.execute(
                """
                SET QUOTED_IDENTIFIER ON;
                IF SCHEMA_ID('pgproof_temporal') IS NULL
                    EXEC('CREATE SCHEMA pgproof_temporal');
                IF OBJECT_ID('pgproof_temporal.accounts','U') IS NOT NULL
                BEGIN
                    ALTER TABLE pgproof_temporal.accounts
                        SET (SYSTEM_VERSIONING = OFF);
                    IF OBJECT_ID('pgproof_temporal.accounts_history','U') IS NOT NULL
                        DROP TABLE pgproof_temporal.accounts_history;
                    DROP TABLE pgproof_temporal.accounts;
                END;
                CREATE TABLE pgproof_temporal.accounts (
                    id int IDENTITY(1,1) NOT NULL
                        CONSTRAINT temporal_accounts_pk PRIMARY KEY,
                    name nvarchar(80) NOT NULL,
                    balance decimal(12,2) NOT NULL
                        CONSTRAINT temporal_balance CHECK (balance >= 0),
                    valid_from datetime2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL,
                    valid_to datetime2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL,
                    PERIOD FOR SYSTEM_TIME (valid_from, valid_to)
                )
                WITH (SYSTEM_VERSIONING = ON (
                    HISTORY_TABLE = pgproof_temporal.accounts_history
                ));
                """
            )
        connection.commit()
        catalog = filter_catalog(
            introspect(connection), schemas=["pgproof_temporal"]
        )
    finally:
        connection.close()

    table = catalog.table("pgproof_temporal.accounts")
    assert table.temporal is not None
    assert table.temporal.history_table == "pgproof_temporal.accounts_history"
    assert table.column("valid_from").generated == "system"
    assert table.column("valid_to").generated == "system"

    from pgproof.adapters.sqlserver.temporal import populate_temporal_history

    connection = connect(MSSQL_DSN)
    writer = SqlServerRowWriter(connection)
    try:
        generated = GenerateDataset(catalog, writer, rows=5, seed=7).execute()
    finally:
        writer.close()

    connection = connect(MSSQL_DSN)
    try:
        updated = populate_temporal_history(connection, catalog)
        with _cursor(connection) as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM pgproof_temporal.accounts_history"
            )
            history = int(cursor.fetchone()[0])
        report = VerifyDataset(
            catalog,
            SqlServerQueryRunner(connection),
            YamlSpecLoader(),
            SqlServerDialect(),
            seed=7,
        ).execute(
            VerifyRequest(dataset_hash=generated.dataset_hash, plan=generated.plan)
        )
    finally:
        connection.close()

    assert updated == 5
    assert history >= 5
    assert report.verdict == "PASS"
    temporal = [item for item in report.results if item.kind == "temporal"]
    assert len(temporal) == 2
    assert all(item.status == "pass" for item in temporal)


def test_sqlserver_bulk_insert(tmp_path) -> None:
    local_dir = os.environ.get("PGPROOF_MSSQL_BULK_LOCAL")
    server_dir = os.environ.get("PGPROOF_MSSQL_BULK_SERVER")
    if not MSSQL_DSN or not local_dir or not server_dir:
        pytest.skip("BULK INSERT staging directories are not configured")

    connection = connect(MSSQL_DSN)
    try:
        with _cursor(connection) as cursor:
            cursor.execute(
                """
                SET QUOTED_IDENTIFIER ON;
                IF SCHEMA_ID('pgproof_bulk') IS NULL
                    EXEC('CREATE SCHEMA pgproof_bulk');
                IF OBJECT_ID('pgproof_bulk.users','U') IS NOT NULL
                    DROP TABLE pgproof_bulk.users;
                CREATE TABLE pgproof_bulk.users (
                    id int IDENTITY(1,1) NOT NULL
                        CONSTRAINT bulk_pk PRIMARY KEY,
                    name nvarchar(80) NOT NULL,
                    amount decimal(12,2) NOT NULL
                        CONSTRAINT bulk_amount CHECK (amount >= 0),
                    active bit NOT NULL
                );
                """
            )
        connection.commit()
        catalog = filter_catalog(introspect(connection), schemas=["pgproof_bulk"])
    finally:
        connection.close()

    from pgproof.adapters.sqlserver.bulk import SqlServerBulkWriter

    connection = connect(MSSQL_DSN)
    writer = SqlServerBulkWriter(
        connection,
        SqlServerDialect(),
        local_dir=local_dir,
        server_dir=server_dir,
    )
    try:
        generated = GenerateDataset(
            catalog, writer, rows=20, seed=3, method="bulk"
        ).execute()
    finally:
        writer.close()

    verify_connection = connect(MSSQL_DSN)
    try:
        report = VerifyDataset(
            catalog,
            SqlServerQueryRunner(verify_connection),
            YamlSpecLoader(),
            SqlServerDialect(),
            seed=3,
        ).execute(
            VerifyRequest(dataset_hash=generated.dataset_hash, plan=generated.plan)
        )
    finally:
        verify_connection.close()

    assert report.verdict == "PASS"
    assert any(Path(local_dir).glob("*.tsv"))


def test_sqlserver_temporal_overlap_is_detected() -> None:
    assert MSSQL_DSN
    schema = "pgproof_temporal_overlap"
    connection = connect(MSSQL_DSN)
    try:
        with _cursor(connection) as cursor:
            cursor.execute(
                f"""
                SET QUOTED_IDENTIFIER ON;
                IF SCHEMA_ID('{schema}') IS NULL
                    EXEC('CREATE SCHEMA {schema}');
                IF OBJECT_ID('{schema}.accounts','U') IS NOT NULL
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM sys.tables
                        WHERE object_id = OBJECT_ID('{schema}.accounts')
                          AND temporal_type = 2
                    )
                        ALTER TABLE {schema}.accounts
                            SET (SYSTEM_VERSIONING = OFF);
                    IF OBJECT_ID('{schema}.accounts_history','U') IS NOT NULL
                        DROP TABLE {schema}.accounts_history;
                    DROP TABLE {schema}.accounts;
                END;
                CREATE TABLE {schema}.accounts (
                    id int NOT NULL CONSTRAINT overlap_accounts_pk PRIMARY KEY,
                    name nvarchar(80) NOT NULL,
                    balance decimal(12,2) NOT NULL,
                    valid_from datetime2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL,
                    valid_to datetime2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL,
                    PERIOD FOR SYSTEM_TIME (valid_from, valid_to)
                )
                WITH (SYSTEM_VERSIONING = ON (
                    HISTORY_TABLE = {schema}.accounts_history
                ));
                INSERT INTO {schema}.accounts (id, name, balance)
                    VALUES (1, N'one', 10);
                UPDATE {schema}.accounts SET balance = balance + 1 WHERE id = 1;
                """
            )
        connection.commit()
        catalog = filter_catalog(introspect(connection), schemas=[schema])
        baseline = VerifyDataset(
            catalog,
            SqlServerQueryRunner(connection),
            YamlSpecLoader(),
            SqlServerDialect(),
            seed=5,
        ).execute(VerifyRequest())
        assert baseline.verdict == "PASS"
        assert any(item.kind == "temporal" for item in baseline.results)

        with _cursor(connection) as cursor:
            cursor.execute(
                f"ALTER TABLE {schema}.accounts SET (SYSTEM_VERSIONING = OFF);"
            )
            cursor.execute(
                f"INSERT INTO {schema}.accounts_history "
                "(id, name, balance, valid_from, valid_to) "
                "VALUES (1, N'fake', 1, '20250101', '99991231');"
            )
        connection.commit()

        corrupted = VerifyDataset(
            catalog,
            SqlServerQueryRunner(connection),
            YamlSpecLoader(),
            SqlServerDialect(),
            seed=5,
        ).execute(VerifyRequest())
    finally:
        with _cursor(connection) as cursor:
            cursor.execute(
                f"""
                IF OBJECT_ID('{schema}.accounts','U') IS NOT NULL
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM sys.tables
                        WHERE object_id = OBJECT_ID('{schema}.accounts')
                          AND temporal_type = 2
                    )
                        ALTER TABLE {schema}.accounts
                            SET (SYSTEM_VERSIONING = OFF);
                    IF OBJECT_ID('{schema}.accounts_history','U') IS NOT NULL
                        DROP TABLE {schema}.accounts_history;
                    DROP TABLE {schema}.accounts;
                END;
                """
            )
        connection.commit()
        connection.close()

    assert corrupted.verdict == "FAIL"
    failed = [
        item
        for item in corrupted.results
        if item.kind == "temporal" and item.status == "fail"
    ]
    assert len(failed) == 2
