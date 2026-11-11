from __future__ import annotations

from pgproof.adapters.postgres.dialect import PostgresDialect
from pgproof.adapters.report.audit_markdown import AuditMarkdownRenderer
from pgproof.application.audit import AuditSchema
from pgproof.core.ir import Catalog, CheckConstraint, Column, ForeignKey, Table, Trigger


def make_catalog() -> Catalog:
    risky = Table(
        schema="public",
        name="events",
        columns=[
            Column("id", "integer", nullable=False),
            Column("account_id", "integer", nullable=True),
            Column("kind", "text", nullable=False),
            Column("payload", "jsonb", nullable=False),
            Column("created_at", "timestamptz", nullable=False),
            Column("updated_at", "timestamptz", nullable=False),
            Column("source", "text", nullable=True),
        ],
        foreign_keys=[
            ForeignKey(
                name="events_account_fkey",
                child_schema="public",
                child_table="events",
                child_columns=["account_id"],
                parent_schema="public",
                parent_table="accounts",
                parent_columns=["id"],
            )
        ],
        checks=[CheckConstraint("kind_check", "CHECK ((kind = 'x'::text))", "kind = 'x'::text")],
        triggers=[
            Trigger(
                name="events_audit",
                timing="AFTER",
                events="INSERT",
                function="audit_event",
                definition="CREATE TRIGGER ...",
            )
        ],
    )
    no_pk = Table(
        schema="public",
        name="imports",
        columns=[
            Column("row_id", "integer", nullable=True),
            Column("payload", "text", nullable=True),
            Column("loaded_at", "timestamptz", nullable=True),
            Column("source", "text", nullable=True),
            Column("batch", "text", nullable=True),
            Column("status", "text", nullable=True),
            Column("error", "text", nullable=True),
        ],
    )
    return Catalog(
        database="app_test",
        server_version="16.15",
        tables=[risky, no_pk],
    )


def test_audit_finds_high_and_medium_risks() -> None:
    audit = AuditSchema(make_catalog(), PostgresDialect()).execute()

    assert audit.tables == 2
    assert audit.foreign_keys == 1
    severities = {risk.severity for risk in audit.risks}
    assert "high" in severities
    assert "medium" in severities
    assert any("No primary key" in risk.finding for risk in audit.risks)
    assert any("Trigger" in risk.finding for risk in audit.risks)
    assert audit.verdict == "high risk"


def test_audit_markdown_contains_the_sections() -> None:
    audit = AuditSchema(make_catalog(), PostgresDialect()).execute()
    markdown = AuditMarkdownRenderer().render(audit)

    assert "# Schema readiness audit" in markdown
    assert "## Inventory" in markdown
    assert "## What pgproof would verify" in markdown
    assert "## Risks" in markdown
    assert "public.imports" in markdown
