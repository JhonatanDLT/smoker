from __future__ import annotations

import pytest

from smoker.core.ir import (
    Catalog,
    Column,
    ForeignKey,
    Table,
    UniqueConstraint,
)


@pytest.fixture
def catalog() -> Catalog:
    accounts = Table(
        schema="public",
        name="accounts",
        columns=[
            Column("id", "integer", nullable=False),
            Column("name", "text", nullable=False),
            Column("created_at", "timestamp with time zone", nullable=False),
        ],
        primary_key=["id"],
    )
    users = Table(
        schema="public",
        name="users",
        columns=[
            Column("id", "integer", nullable=False),
            Column("account_id", "integer", nullable=False),
            Column("email", "character varying(255)", nullable=False),
            Column("status", "text", nullable=False, enum_values=["active", "inactive"]),
        ],
        primary_key=["id"],
        uniques=[UniqueConstraint("users_email_unique", ["email"])],
        foreign_keys=[
            ForeignKey(
                name="users_account_id_fkey",
                child_schema="public",
                child_table="users",
                child_columns=["account_id"],
                parent_schema="public",
                parent_table="accounts",
                parent_columns=["id"],
            )
        ],
    )
    return Catalog(
        database="smoker_test",
        server_version="16.0",
        tables=[accounts, users],
    )


@pytest.fixture
def cyclic_catalog() -> Catalog:
    left = Table(
        schema="public",
        name="left_table",
        columns=[
            Column("id", "integer", nullable=False),
            Column("right_id", "integer", nullable=True),
        ],
        primary_key=["id"],
        foreign_keys=[
            ForeignKey(
                name="left_right_fkey",
                child_schema="public",
                child_table="left_table",
                child_columns=["right_id"],
                parent_schema="public",
                parent_table="right_table",
                parent_columns=["id"],
            )
        ],
    )
    right = Table(
        schema="public",
        name="right_table",
        columns=[
            Column("id", "integer", nullable=False),
            Column("left_id", "integer", nullable=True),
        ],
        primary_key=["id"],
        foreign_keys=[
            ForeignKey(
                name="right_left_fkey",
                child_schema="public",
                child_table="right_table",
                child_columns=["left_id"],
                parent_schema="public",
                parent_table="left_table",
                parent_columns=["id"],
            )
        ],
    )
    return Catalog(
        database="smoker_test",
        server_version="16.0",
        tables=[left, right],
    )
