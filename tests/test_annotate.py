from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "annotate.py"


def _report(results: list[dict]) -> dict:
    return {
        "verdict": "FAIL" if any(r["status"] in {"fail", "error"} for r in results) else "PASS",
        "seed": 42,
        "total_rows": 100,
        "duration_seconds": 0.1,
        "dataset_hash": "a" * 64,
        "reproduce": "smoker run --rows 100",
        "summary": {
            "total": len(results),
            "passed": sum(r["status"] == "pass" for r in results),
            "failed": sum(r["status"] == "fail" for r in results),
            "error": sum(r["status"] == "error" for r in results),
            "skipped": sum(r["status"] == "skipped" for r in results),
            "violations": sum(r.get("violations", 0) for r in results),
        },
        "invariants": results,
        "repairs": {
            "actions": [
                {
                    "id": "invoice_total_matches_lines",
                    "pass_number": 1,
                    "before": 10,
                    "after": 0,
                    "rows_affected": 10,
                }
            ],
            "applied": 10,
            "residual": 0,
        },
    }


def _run(tmp_path: Path, results: list[dict], policy: str) -> tuple[str, str]:
    report_path = tmp_path / "report.json"
    summary_path = tmp_path / "summary.md"
    report_path.write_text(json.dumps(_report(results)), encoding="utf-8")
    env = {**os.environ, "GITHUB_STEP_SUMMARY": str(summary_path)}
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--report",
            str(report_path),
            "--policy",
            policy,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return completed.stdout, summary_path.read_text(encoding="utf-8")


def test_failures_become_errors(tmp_path: Path) -> None:
    stdout, summary = _run(
        tmp_path,
        [
            {
                "id": "check:t:1",
                "status": "fail",
                "table": "public.t",
                "violations": 3,
                "description": "amount is positive",
            }
        ],
        "fail",
    )
    assert "::error title=check%3At%3A1::" in stdout
    assert "### Failures" in summary
    assert "### Repairs" in summary


def test_shadow_mode_uses_warnings(tmp_path: Path) -> None:
    stdout, _ = _run(
        tmp_path,
        [
            {
                "id": "check:t:1",
                "status": "fail",
                "table": "public.t",
                "violations": 3,
                "description": "amount is positive",
            }
        ],
        "never",
    )
    assert "::warning title=check%3At%3A1::" in stdout


def test_skipped_becomes_notice(tmp_path: Path) -> None:
    stdout, summary = _run(
        tmp_path,
        [
            {
                "id": "trigger:t:1",
                "status": "skipped",
                "table": "public.t",
                "note": "trigger effects are not verified",
            }
        ],
        "fail",
    )
    assert "::notice title=trigger%3At%3A1::" in stdout
    assert "### Not verified" in summary


def test_newlines_and_percent_are_escaped(tmp_path: Path) -> None:
    stdout, _ = _run(
        tmp_path,
        [
            {
                "id": "check:t:1",
                "status": "fail",
                "table": "public.t",
                "violations": 1,
                "description": "line one\nline two 100%",
            }
        ],
        "fail",
    )
    assert "%0A" in stdout
    assert "%25" in stdout
