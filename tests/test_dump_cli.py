from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from smoker.cli import app

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "examples/saas-billing/schema.sql"


def test_load_dump_command(tmp_path: Path) -> None:
    out = tmp_path / "catalog.json"
    result = CliRunner().invoke(app, ["load-dump", str(SCHEMA), "-o", str(out)])
    assert result.exit_code == 0, result.output
    catalog = json.loads(out.read_text(encoding="utf-8"))
    assert len(catalog["tables"]) == 10
    assert catalog["tables"][0]["provenance"]["kind"] == "dump"
    assert catalog["rules"][0]["provenance"]["kind"] == "trigger"
    assert catalog["rules"][0]["applied"] is False


def test_plan_works_from_a_dump_without_a_database(tmp_path: Path) -> None:
    out = tmp_path / "plan.json"
    result = CliRunner().invoke(
        app,
        ["plan", "--schema-file", str(SCHEMA), "--rows", "10", "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert plan["total_rows"] == 100


def test_audit_works_from_a_dump_without_a_database(tmp_path: Path) -> None:
    out = tmp_path / "audit.md"
    result = CliRunner().invoke(
        app, ["audit", "--schema-file", str(SCHEMA), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "Schema readiness audit" in out.read_text(encoding="utf-8")
