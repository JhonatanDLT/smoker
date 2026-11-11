from __future__ import annotations

from smoker.core.filters import filter_catalog, split_patterns
from smoker.core.ir import Catalog, Table


def make_catalog() -> Catalog:
    return Catalog(
        database="x",
        server_version="16",
        tables=[
            Table(schema="public", name="users"),
            Table(schema="public", name="invoices"),
            Table(schema="billing", name="invoices"),
            Table(schema="billing", name="plans"),
        ],
    )


def test_schema_filter() -> None:
    filtered = filter_catalog(make_catalog(), schemas=["billing"])
    assert [table.key for table in filtered.tables] == [
        "billing.invoices",
        "billing.plans",
    ]
    assert filtered.schemas == ["billing"]


def test_include_glob_on_qualified_name() -> None:
    filtered = filter_catalog(make_catalog(), include=["billing.*"])
    assert [table.key for table in filtered.tables] == [
        "billing.invoices",
        "billing.plans",
    ]


def test_include_glob_on_bare_name() -> None:
    filtered = filter_catalog(make_catalog(), include=["invoices"])
    assert [table.key for table in filtered.tables] == [
        "public.invoices",
        "billing.invoices",
    ]


def test_exclude_glob() -> None:
    filtered = filter_catalog(make_catalog(), exclude=["*.invoices"])
    assert [table.key for table in filtered.tables] == [
        "public.users",
        "billing.plans",
    ]


def test_include_and_exclude_are_combined() -> None:
    filtered = filter_catalog(
        make_catalog(), include=["*"], exclude=["public.*"]
    )
    assert [table.key for table in filtered.tables] == [
        "billing.invoices",
        "billing.plans",
    ]


def test_split_patterns() -> None:
    assert split_patterns(" billing, public.users ,, ") == ["billing", "public.users"]
    assert split_patterns(None) == []
