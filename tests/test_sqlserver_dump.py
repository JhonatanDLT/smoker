from __future__ import annotations

from pgproof.adapters.sqlserver.dialect import SqlServerDialect
from pgproof.adapters.sqlserver.dump import SqlServerDumpLoader, split_batches
from pgproof.application.audit import AuditSchema
from pgproof.core.creation import creation_order

DUMP = """
CREATE SCHEMA [shop];
GO
CREATE TABLE [shop].[customers] (
    [id] int IDENTITY(1,1) NOT NULL CONSTRAINT [customers_pk] PRIMARY KEY,
    [name] nvarchar(100) NOT NULL,
    [code] int NULL,
    [active] bit NOT NULL DEFAULT 1,
    [created_at] datetime2(3) NOT NULL DEFAULT SYSUTCDATETIME()
);
GO
CREATE TABLE [shop].[orders] (
    [id] int IDENTITY(1,1) NOT NULL CONSTRAINT [orders_pk] PRIMARY KEY,
    [customer_id] int NOT NULL,
    [total] decimal(12,2) NOT NULL
        CONSTRAINT [orders_total] CHECK ([total] >= 0),
    [doubled] AS ([total] * 2) PERSISTED,
    [version] rowversion,
    CONSTRAINT [orders_customer_fk] FOREIGN KEY ([customer_id])
        REFERENCES [shop].[customers]([id])
);
GO
CREATE UNIQUE INDEX [orders_customer_uq] ON [shop].[orders]([customer_id]);
GO
CREATE UNIQUE INDEX [customers_code_uq] ON [shop].[customers]([code])
    WHERE [code] IS NOT NULL;
GO
CREATE VIEW [shop].[v_orders] AS
    SELECT [id], [total] FROM [shop].[orders];
GO
CREATE PROCEDURE [shop].[p_orders]
    @customer_id int,
    @min decimal(12,2) = 0
AS
BEGIN
    SET NOCOUNT ON;
    IF @min < 0 THROW 50001, 'negative', 1;
    SELECT [id] FROM [shop].[orders]
    WHERE [customer_id] = @customer_id AND [total] >= @min;
END;
GO
CREATE TRIGGER [shop].[orders_guard] ON [shop].[orders]
AFTER INSERT, UPDATE AS
BEGIN
    IF EXISTS (SELECT 1 FROM inserted WHERE [total] < 0)
        THROW 50002, 'bad total', 1;
END;
GO
CREATE SEQUENCE [shop].[ticket_seq] START WITH 100 INCREMENT BY 1;
GO
CREATE TYPE [shop].[phone] FROM nvarchar(20) NOT NULL;
GO
"""


def test_split_batches_on_go() -> None:
    assert len(split_batches(DUMP)) == 10


def test_tables_columns_constraints_and_provenance() -> None:
    catalog = SqlServerDumpLoader().load(DUMP)
    orders = catalog.table("shop.orders")
    customers = catalog.table("shop.customers")

    assert customers.column("id").identity == "a"
    assert orders.column("doubled").generated == "computed"
    assert orders.column("version").generated == "rowversion"
    assert orders.column("doubled").writable is False
    assert orders.column("version").writable is False
    assert orders.primary_key == ["id"]
    assert [unique.columns for unique in orders.uniques] == [["customer_id"]]
    assert [check.predicate for check in orders.checks] == ["[total] >= 0"]
    assert orders.foreign_keys[0].parent_table == "customers"
    assert orders.foreign_keys[0].child_columns == ["customer_id"]
    assert catalog.table("shop.orders").provenance is not None
    assert customers.uniques[0].predicate == "NOT [code] IS NULL"


def test_view_procedure_trigger_sequence_and_type() -> None:
    catalog = SqlServerDumpLoader().load(DUMP)
    objects = {(obj.kind, obj.name): obj for obj in catalog.objects}

    assert objects[("view", "v_orders")].depends_on == ["shop.orders"]
    assert objects[("sequence", "ticket_seq")].schema == "shop"
    assert objects[("type", "phone")].schema == "shop"

    routine = catalog.functions[0]
    assert routine.kind == "procedure"
    assert routine.language == "tsql"
    assert routine.parameters == ["@customer_id int", "@min decimal(12,2) = 0"]
    assert "THROW 50001" in routine.body
    assert routine.provenance is not None

    trigger = catalog.table("shop.orders").triggers[0]
    assert trigger.name == "orders_guard"
    assert trigger.timing == "AFTER"
    assert trigger.events == "INSERT, UPDATE"
    assert "THROW 50002" in (trigger.body or "")

    assert any(
        rule.expression == "@min >= 0" and rule.provenance.kind == "function"
        for rule in catalog.rules
    )


def test_creation_order_and_audit_include_procedures() -> None:
    catalog = SqlServerDumpLoader().load(DUMP)
    order = creation_order(catalog)
    phases = [step.phase for step in order.steps]

    assert "procedure" in phases
    assert phases.index("procedure") > phases.index("table")
    assert any(
        step.phase == "procedure" and step.name == "shop.p_orders"
        for step in order.steps
    )

    audit = AuditSchema(catalog, SqlServerDialect()).execute()
    assert audit.functions == 0
    assert audit.procedures == 1
    assert any("stored procedures" in risk.finding for risk in audit.risks)


def test_load_dump_cli_with_sqlserver_dialect(tmp_path) -> None:
    from typer.testing import CliRunner

    from pgproof.cli import app

    source = tmp_path / "schema.sql"
    source.write_text(DUMP, encoding="utf-8")
    out = tmp_path / "catalog.json"
    result = CliRunner().invoke(
        app,
        [
            "load-dump",
            "--dialect",
            "sqlserver",
            str(source),
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    import json

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert any(item["kind"] == "procedure" for item in payload["functions"])
    assert any(item["kind"] == "view" for item in payload["objects"])
    assert any(step["phase"] == "procedure" for step in payload["creation_order"]["steps"])


def test_multi_database_dump_with_use_and_three_part_names() -> None:
    dump = """
    USE [db_a];
    GO
    CREATE TABLE [dbo].[users] (
        [id] int IDENTITY PRIMARY KEY,
        [name] nvarchar(50) NOT NULL
    );
    GO
    USE [db_b];
    GO
    CREATE TABLE [dbo].[orders] (
        [id] int IDENTITY PRIMARY KEY,
        [user_id] int NOT NULL,
        [amount] decimal(10,2) NOT NULL
    );
    GO
    CREATE VIEW [db_b].[dbo].[v_orders] AS
        SELECT o.[id], u.[name]
        FROM [db_b].[dbo].[orders] o
        JOIN [db_a].[dbo].[users] u ON u.[id] = o.[user_id];
    GO
    """
    catalog = SqlServerDumpLoader().load(dump)

    assert catalog.databases == ["db_a", "db_b"]
    assert catalog.database == "multi"
    users = catalog.table("db_a.dbo.users")
    orders = catalog.table("db_b.dbo.orders")
    assert users.database == "db_a"
    assert orders.database == "db_b"
    assert orders.foreign_keys[0].parent_key == "db_a.dbo.users"
    assert orders.foreign_keys[0].inferred is True
    view = next(obj for obj in catalog.objects if obj.kind == "view")
    assert view.database == "db_b"
    assert view.depends_on == ["db_b.dbo.orders", "db_a.dbo.users"]
