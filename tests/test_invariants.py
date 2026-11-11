from __future__ import annotations

from smoker.adapters.postgres.dialect import PostgresDialect
from smoker.core.invariants import (
    extract_predicate,
    infer_semantic,
    invariants_from_catalog,
    parse_local_rules,
)
from smoker.core.ir import Catalog, CheckConstraint, Column, Table


def test_extract_predicate_simple() -> None:
    assert extract_predicate("CHECK ((amount > 0))") == "(amount > 0)"


def test_extract_predicate_not_valid() -> None:
    assert extract_predicate("CHECK ((total >= 0)) NOT VALID") == "(total >= 0)"


def test_extract_predicate_any_array() -> None:
    definition = "CHECK ((status = ANY (ARRAY['draft'::text, 'open'::text])))"
    assert extract_predicate(definition) == "(status = ANY (ARRAY['draft'::text, 'open'::text]))"


def test_infer_semantic_by_name() -> None:
    assert infer_semantic(Column("email", "text")) == "email"
    assert infer_semantic(Column("created_at", "timestamptz")) == "timestamp"
    assert infer_semantic(Column("total_amount", "numeric")) == "amount"
    assert infer_semantic(Column("x", "text")) is None


def test_local_rules_range_and_ordering() -> None:
    table = Table(
        schema="public",
        name="t",
        columns=[
            Column("id", "integer", nullable=False),
            Column("quantity", "integer", nullable=False),
            Column("starts_at", "timestamp", nullable=False),
            Column("ends_at", "timestamp", nullable=True),
        ],
        checks=[
            CheckConstraint("c1", "CHECK ((quantity > 0))", "(quantity > 0)"),
            CheckConstraint("c2", "CHECK ((ends_at > starts_at))", "(ends_at > starts_at)"),
        ],
    )
    rules = parse_local_rules(table)
    assert rules.ranges["quantity"] == (0.0, None, True, False)
    assert ("ends_at", "starts_at", True) in rules.orderings


def test_local_rules_derived() -> None:
    table = Table(
        schema="public",
        name="lines",
        columns=[
            Column("quantity", "integer", nullable=False),
            Column("unit_price", "numeric", nullable=False),
            Column("amount", "numeric", nullable=False),
        ],
        checks=[
            CheckConstraint(
                "c",
                "CHECK ((amount = quantity * unit_price))",
                "(amount = quantity * unit_price)",
            )
        ],
    )
    rules = parse_local_rules(table)
    assert ("amount", "quantity", "*", "unit_price") in rules.derived


def test_catalog_invariants_cover_constraints(catalog: Catalog) -> None:
    invariants = invariants_from_catalog(catalog, PostgresDialect())
    kinds = {invariant.kind.value for invariant in invariants}
    assert "not_null" in kinds
    assert "unique" in kinds
    assert "foreign_key" in kinds
    assert "enum" in kinds
    foreign_key = next(i for i in invariants if i.kind.value == "foreign_key")
    assert "NOT EXISTS" in foreign_key.violation_sql


def test_bracketed_arithmetic_is_not_duplicated_as_copy() -> None:
    table = Table(
        schema="dbo",
        name="invoice_lines",
        columns=[
            Column("quantity", "int", nullable=False),
            Column("unit_price", "decimal(12,2)", nullable=False),
            Column("amount", "decimal(12,2)", nullable=False),
        ],
        checks=[
            CheckConstraint(
                "amount_check",
                "CHECK ([amount]=[quantity]*[unit_price])",
                None,
            )
        ],
    )
    rules = parse_local_rules(table)

    assert rules.derived == [("amount", "quantity", "*", "unit_price")]


def test_or_equality_chain_becomes_allowed_values() -> None:
    table = Table(
        schema="dbo",
        name="accounts",
        columns=[Column("status", "nvarchar(20)", nullable=False)],
        checks=[
            CheckConstraint(
                "status_check",
                "CHECK ([status]='cancelled' OR [status]='past_due' OR "
                "[status]='active' OR [status]='trial')",
                None,
            )
        ],
    )
    rules = parse_local_rules(table)

    assert rules.allowed["status"] == {"cancelled", "past_due", "active", "trial"}
    assert rules.unparsed == []
