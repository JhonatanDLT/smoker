from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from smoker.adapters.postgres.dump import DumpLoader
from smoker.cli import app
from smoker.core.creation import creation_order

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "examples/saas-billing/schema.sql"

PHASE_RANK = {
    "extension": 0,
    "enum": 1,
    "type": 2,
    "domain": 3,
    "sequence": 4,
    "table": 5,
    "function": 6,
    "index": 7,
    "trigger": 8,
    "view": 9,
    "materialized_view": 10,
    "rule": 11,
    "policy": 12,
    "comment": 13,
    "grant": 14,
}

DUMP = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA app;
SET search_path TO app;
CREATE TYPE status AS ENUM ('new', 'done');
CREATE TYPE address AS (street text, city text);
CREATE DOMAIN positive_int AS integer CHECK (VALUE > 0);
CREATE SEQUENCE events_seq;
CREATE TABLE parents (
    id integer PRIMARY KEY,
    status status,
    amount positive_int
);
CREATE TABLE children (
    id integer PRIMARY KEY,
    parent_id integer NOT NULL REFERENCES parents (id)
);
CREATE INDEX children_parent_idx ON children (parent_id);
CREATE FUNCTION touch() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RETURN NEW;
END;
$$;
CREATE TRIGGER children_touch BEFORE UPDATE ON children
    FOR EACH ROW EXECUTE FUNCTION touch();
CREATE VIEW v_base AS SELECT id FROM parents;
CREATE VIEW v_top AS
    SELECT b.id FROM v_base b JOIN children c ON c.id = b.id;
CREATE RULE parents_rule AS ON INSERT TO parents DO ALSO NOTIFY parents_insert;
CREATE POLICY parents_policy ON parents USING (true);
"""


def test_creation_order_respects_phases_and_dependencies() -> None:
    catalog = DumpLoader().load(DUMP)
    order = creation_order(catalog)
    steps = {(step.phase, step.name): step for step in order.steps}

    ranks = [PHASE_RANK[step.phase] for step in order.steps]
    assert ranks == sorted(ranks)

    assert ("extension", "pgcrypto") in steps
    assert ("enum", "app.status") in steps
    assert ("type", "app.address") in steps
    assert ("domain", "app.positive_int") in steps
    assert ("sequence", "app.events_seq") in steps

    assert steps[("table", "app.children")].depends_on == ["app.parents"]
    assert steps[("index", "app.children_parent_idx")].depends_on == ["app.children"]
    assert steps[("trigger", "app.children.children_touch")].depends_on == [
        "app.children",
        "app.touch",
    ]
    assert steps[("view", "app.v_base")].depends_on == ["app.parents"]
    assert set(steps[("view", "app.v_top")].depends_on) == {"app.v_base", "app.children"}
    assert steps[("rule", "app.parents_rule")].depends_on == ["app.parents"]
    assert steps[("policy", "app.parents_policy")].depends_on == ["app.parents"]


def test_views_are_ordered_by_dependency_not_name() -> None:
    catalog = DumpLoader().load(
        """
        CREATE SCHEMA app;
        SET search_path TO app;
        CREATE TABLE base (id integer PRIMARY KEY);
        CREATE VIEW z_base AS SELECT id FROM base;
        CREATE VIEW a_top AS SELECT id FROM z_base;
        """
    )
    order = creation_order(catalog)
    views = [step.name for step in order.steps if step.phase == "view"]

    assert views == ["app.z_base", "app.a_top"]


def test_creation_order_reports_foreign_key_cycles() -> None:
    catalog = DumpLoader().load(
        """
        CREATE TABLE a (id integer PRIMARY KEY, b_id integer);
        CREATE TABLE b (id integer PRIMARY KEY, a_id integer);
        ALTER TABLE a ADD FOREIGN KEY (b_id) REFERENCES b (id);
        ALTER TABLE b ADD FOREIGN KEY (a_id) REFERENCES a (id);
        """
    )
    order = creation_order(catalog)
    tables = [step for step in order.steps if step.phase == "table"]

    assert {step.name for step in tables} == {"public.a", "public.b"}
    assert any(step.cycle for step in tables)
    assert order.cycles


def test_order_command_prints_text() -> None:
    result = CliRunner().invoke(
        app, ["order", "--schema-file", str(SCHEMA), "-o", "-"]
    )

    assert result.exit_code == 0, result.output
    assert "table" in result.output
    assert "billing.accounts" in result.output
    assert "trigger" in result.output


def test_order_command_writes_json(tmp_path: Path) -> None:
    out = tmp_path / "order.json"
    result = CliRunner().invoke(
        app,
        [
            "order",
            "--schema-file",
            str(SCHEMA),
            "--format",
            "json",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output

    import json

    payload = json.loads(out.read_text(encoding="utf-8"))
    phases = [step["phase"] for step in payload["steps"]]
    assert phases == sorted(phases, key=lambda phase: PHASE_RANK[phase])
