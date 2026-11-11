from __future__ import annotations

from pathlib import Path

from pgproof.adapters.registry import available_dialects, get_adapter
from pgproof.adapters.script import SqlFileWriter
from pgproof.adapters.sqlserver.dialect import (
    SqlServerDialect,
    strip_check_definition,
    type_text,
)
from pgproof.core.invariants import invariants_from_catalog
from pgproof.core.ir import (
    Catalog,
    CheckConstraint,
    Column,
    ForeignKey,
    Table,
)


def make_catalog() -> Catalog:
    accounts = Table(
        schema="dbo",
        name="accounts",
        columns=[
            Column("id", "int", nullable=False),
            Column("email", "nvarchar(200)", nullable=False),
            Column("parent_id", "int", nullable=True),
            Column("name", "nvarchar(100)", nullable=True),
        ],
        primary_key=["id"],
        foreign_keys=[
            ForeignKey(
                name="accounts_parent_fk",
                child_schema="dbo",
                child_table="accounts",
                child_columns=["parent_id"],
                parent_schema="dbo",
                parent_table="accounts",
                parent_columns=["id"],
            )
        ],
        checks=[
            CheckConstraint(
                "accounts_name_check",
                "CHECK (([name] IS NULL OR len([name]) > 0))",
                "([name] IS NULL OR len([name]) > 0)",
            )
        ],
    )
    return Catalog(
        database="demo",
        server_version="16.0",
        tables=[accounts],
        schemas=["dbo"],
    )


def test_quoting_literals_and_types() -> None:
    dialect = SqlServerDialect()

    assert dialect.quote_ident("order]detail") == "[order]]detail]"
    assert dialect.quote_qualified("dbo", "users") == "[dbo].[users]"
    assert dialect.literal("O'Reilly") == "N'O''Reilly'"
    assert dialect.literal(True) == "1"
    assert dialect.literal(None) == "NULL"

    assert dialect.classify_type("bit").value == "boolean"
    assert dialect.classify_type("uniqueidentifier").value == "uuid"
    assert dialect.classify_type("datetime2").value == "timestamp"
    assert dialect.classify_type("nvarchar(50)").value == "text"
    assert dialect.classify_type("varbinary(max)").value == "bytea"

    assert type_text("nvarchar", 200, 0, 0) == "nvarchar(100)"
    assert type_text("varbinary", -1, 0, 0) == "varbinary(max)"
    assert type_text("decimal", 9, 18, 2) == "decimal(18,2)"


def test_check_definition_is_stripped() -> None:
    assert strip_check_definition("([price]>(0))") == "[price]>(0)"
    assert strip_check_definition("((len([name])>(0)))") == "len([name])>(0)"


def test_invariants_render_tsql() -> None:
    catalog = make_catalog()
    invariants = invariants_from_catalog(catalog, SqlServerDialect())
    by_kind = {item.kind.value: item for item in invariants}

    assert "[dbo].[accounts]" in by_kind["not_null"].violation_sql
    assert "COUNT_BIG(*) AS duplicates" in by_kind["unique"].violation_sql
    assert "NOT EXISTS" in by_kind["foreign_key"].violation_sql
    assert "WHERE NOT (" in by_kind["check"].violation_sql
    assert "ctid" not in " ".join(item.violation_sql for item in invariants)

    assert "sqlserver" in available_dialects()
    assert get_adapter("sqlserver").dialect.name == "sqlserver"


def test_scripts_use_tsql_constructs() -> None:
    dialect = SqlServerDialect()
    catalog = make_catalog()
    table = catalog.table("dbo.accounts")

    assert dialect.script_begin() == [
        "SET ANSI_NULLS ON;",
        "SET QUOTED_IDENTIFIER ON;",
        "SET XACT_ABORT ON;",
        "BEGIN TRANSACTION;",
    ]
    assert dialect.script_reset_sequences([table]) == []
    assert dialect.script_before_insert(table) == []
    assert dialect.script_after_insert(table) == []

    identity = Table(
        schema="dbo",
        name="events",
        columns=[Column("id", "bigint", identity="a", nullable=False)],
        primary_key=["id"],
    )
    statements = dialect.script_reset_sequences([identity])
    assert any("DBCC CHECKIDENT" in statement for statement in statements)
    assert dialect.script_before_insert(identity) == [
        "SET IDENTITY_INSERT [dbo].[events] ON;"
    ]
    assert dialect.script_after_insert(identity) == [
        "SET IDENTITY_INSERT [dbo].[events] OFF;"
    ]


def test_script_truncate_deletes_children_first() -> None:
    dialect = SqlServerDialect()
    parent = Table(
        schema="dbo",
        name="parents",
        columns=[Column("id", "int", nullable=False)],
        primary_key=["id"],
    )
    child = Table(
        schema="dbo",
        name="children",
        columns=[
            Column("id", "int", nullable=False),
            Column("parent_id", "int", nullable=False),
        ],
        primary_key=["id"],
        foreign_keys=[
            ForeignKey(
                name="children_parent_fk",
                child_schema="dbo",
                child_table="children",
                child_columns=["parent_id"],
                parent_schema="dbo",
                parent_table="parents",
                parent_columns=["id"],
            )
        ],
    )
    statements = dialect.script_truncate([parent, child])

    assert statements == ["DELETE FROM [dbo].[children];", "DELETE FROM [dbo].[parents];"]


def test_sql_script_writer_uses_tsql(tmp_path: Path) -> None:
    catalog = make_catalog()
    table = catalog.table("dbo.accounts")
    out = tmp_path / "data.sql"
    writer = SqlFileWriter(out, SqlServerDialect(), truncate=False)
    try:
        writer.write(table, iter([(1, "a@example.com", None, "Ana")]))
        writer.commit()
    finally:
        writer.close()
    text = out.read_text(encoding="utf-8")

    assert "SET XACT_ABORT ON;" in text
    assert "BEGIN TRANSACTION;" in text
    assert 'INSERT INTO [dbo].[accounts] ([id], [email], [parent_id], [name]) VALUES' in text
    assert "N'a@example.com'" in text
    assert "COMMIT TRANSACTION;" in text
