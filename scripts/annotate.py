#!/usr/bin/env python3
"""Turn a pgproof report into GitHub annotations and a job summary.

GitHub does not display JUnit XML natively. This script emits workflow commands
(``::error`` / ``::warning`` / ``::notice``) and appends a Markdown summary to
``$GITHUB_STEP_SUMMARY``. The JUnit file is still written for third-party
reporters.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def escape_data(value: Any) -> str:
    return str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(value: Any) -> str:
    return escape_data(value).replace(":", "%3A").replace(",", "%2C")


def _level(policy: str) -> str:
    return "warning" if policy == "never" else "error"


def annotate(report: dict[str, Any], policy: str) -> None:
    for invariant in report.get("invariants", []):
        status = invariant.get("status")
        raw_id = invariant.get("id", "invariant")
        identifier = escape_property(raw_id)
        table = invariant.get("table", "")
        if status == "fail":
            message = (
                f"{raw_id} on {table}: {invariant.get('violations', 0)} "
                f"violating rows. {invariant.get('description', '')}"
            )
            print(f"::{_level(policy)} title={identifier}::{escape_data(message)}")
        elif status == "error":
            message = (
                f"{raw_id} on {table}: verification failed. "
                f"{invariant.get('error') or 'unknown error'}"
            )
            print(f"::{_level(policy)} title={identifier}::{escape_data(message)}")
        elif status == "skipped":
            message = f"{raw_id} on {table}: not verified. {invariant.get('note') or ''}"
            print(f"::notice title={identifier}::{escape_data(message)}")


def summary_markdown(report: dict[str, Any], policy: str) -> str:
    summary = report.get("summary", {})
    repairs = report.get("repairs", {})
    verdict = report.get("verdict", "FAIL")
    icon = "✅" if verdict == "PASS" else "❌"
    lines = [
        f"## {icon} pgproof: {verdict}",
        "",
        (
            f"Policy: `{policy}` · seed `{report.get('seed')}` · "
            f"{report.get('total_rows', 0):,} rows · {report.get('duration_seconds')}s"
        ),
        "",
        "| Total | Passed | Failed | Errors | Skipped | Violations |",
        "|---:|---:|---:|---:|---:|---:|",
        (
            f"| {summary.get('total', 0)} | {summary.get('passed', 0)} | "
            f"{summary.get('failed', 0)} | {summary.get('error', 0)} | "
            f"{summary.get('skipped', 0)} | {summary.get('violations', 0)} |"
        ),
        "",
    ]

    failures = [item for item in report.get("invariants", []) if item.get("status") == "fail"]
    if failures:
        lines.extend(["### Failures", "", "| Rule | Table | Violations |", "|---|---|---:|"])
        for item in failures[:20]:
            lines.append(
                f"| `{item.get('id')}` | `{item.get('table')}` | {item.get('violations', 0)} |"
            )
        if len(failures) > 20:
            lines.append(f"| … | … | {len(failures) - 20} more |")
        lines.append("")

    actions = repairs.get("actions", [])
    if actions:
        lines.extend(
            [
                "### Repairs",
                "",
                (
                    f"{repairs.get('applied', 0)} rows across {len(actions)} actions; "
                    f"residual {repairs.get('residual', 0)}."
                ),
                "",
                "| Rule | Pass | Before | After | Rows |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for action in actions:
            lines.append(
                f"| `{action.get('id')}` | {action.get('pass_number')} | "
                f"{action.get('before')} | {action.get('after')} | "
                f"{action.get('rows_affected')} |"
            )
        lines.append("")

    skipped = [item for item in report.get("invariants", []) if item.get("status") == "skipped"]
    if skipped:
        lines.extend(["### Not verified", ""])
        for item in skipped[:10]:
            lines.append(f"- `{item.get('id')}` — {item.get('note') or 'not verified'}")
        lines.append("")

    lines.extend(
        [
            "### Reproduce",
            "",
            "```bash",
            report.get("reproduce", "pgproof run ..."),
            "```",
            "",
            f"Dataset hash: `{report.get('dataset_hash', '')}`",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--policy", default="fail", choices=["never", "error", "fail"])
    parser.add_argument(
        "--summary-file",
        default=os.environ.get("GITHUB_STEP_SUMMARY"),
        help="Defaults to $GITHUB_STEP_SUMMARY",
    )
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    annotate(report, args.policy)
    markdown = summary_markdown(report, args.policy)

    if args.summary_file:
        with open(args.summary_file, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
