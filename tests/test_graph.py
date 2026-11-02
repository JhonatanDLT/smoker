from __future__ import annotations

from pgproof.core.graph import build_edges, generation_order
from pgproof.core.ir import Catalog


def test_parent_generates_before_child(catalog: Catalog) -> None:
    info = generation_order(catalog)
    assert info.order.index("public.accounts") < info.order.index("public.users")
    assert not info.cycles


def test_edges_point_to_parents(catalog: Catalog) -> None:
    edges = build_edges(catalog)
    assert edges["public.users"] == {"public.accounts"}
    assert edges["public.accounts"] == set()


def test_detects_two_table_cycle(cyclic_catalog: Catalog) -> None:
    info = generation_order(cyclic_catalog)
    assert info.cycles == [["public.left_table", "public.right_table"]]
    assert set(info.order) == {"public.left_table", "public.right_table"}


def test_self_reference_is_not_a_cycle() -> None:
    from pgproof.core.ir import Column, ForeignKey, Table

    table = Table(
        schema="public",
        name="accounts",
        columns=[Column("id", "integer", nullable=False)],
        primary_key=["id"],
        foreign_keys=[
            ForeignKey(
                name="accounts_parent_fkey",
                child_schema="public",
                child_table="accounts",
                child_columns=["id"],
                parent_schema="public",
                parent_table="accounts",
                parent_columns=["id"],
            )
        ],
    )
    catalog = Catalog(database="x", server_version="16", tables=[table])
    info = generation_order(catalog)
    assert info.cycles == []
    assert info.order == ["public.accounts"]
