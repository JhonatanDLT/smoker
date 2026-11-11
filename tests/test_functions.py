"""Trigger functions are stored once per OID, not once per trigger."""

from __future__ import annotations

import os
import subprocess

import pytest

from smoker.adapters.postgres.introspect import connect, introspect

pytestmark = pytest.mark.integration

DSN = os.environ.get("SMOKER_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="SMOKER_TEST_DSN is not set")
def test_trigger_functions_are_deduplicated() -> None:
    assert DSN
    statements = [
        "DROP SCHEMA IF EXISTS fn CASCADE;",
        "CREATE SCHEMA fn;",
        (
            "CREATE OR REPLACE FUNCTION fn.touch() RETURNS trigger LANGUAGE plpgsql AS $$"
            " BEGIN NEW.updated_at := now(); RETURN NEW; END; $$;"
        ),
    ]
    for index in range(1, 6):
        statements.append(
            f"CREATE TABLE fn.t{index} ("
            "id integer PRIMARY KEY, updated_at timestamptz NOT NULL);"
        )
        statements.append(
            f"CREATE TRIGGER t{index}_touch BEFORE UPDATE ON fn.t{index} "
            "FOR EACH ROW EXECUTE FUNCTION fn.touch();"
        )

    path = "/tmp/smoker_functions_test.sql"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(statements))
    subprocess.run(
        ["psql", DSN, "-q", "-v", "ON_ERROR_STOP=1", "-f", path],
        check=True,
        capture_output=True,
    )

    with connect(DSN) as conn:
        catalog = introspect(conn)

    tables = [table for table in catalog.tables if table.schema == "fn"]
    assert len(tables) == 5
    oids = {
        trigger.function_oid
        for table in tables
        for trigger in table.triggers
    }
    assert len(oids) == 1
    assert sum(1 for function in catalog.functions if function.oid in oids) == 1
    assert all(trigger.body is None for table in tables for trigger in table.triggers)
