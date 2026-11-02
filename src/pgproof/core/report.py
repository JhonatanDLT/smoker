"""Verification report: the artifact that makes pgproof sellable.

The JSON is canonical. The HTML is a rendering of it. Everything an auditor or
a reviewer needs must be present in the JSON, including what was NOT verified.
"""

from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .ir import Catalog
from .planner import Plan
from .verifier import InvariantResult, Verification


DEFAULT_LIMITATIONS = [
    "Triggers are inventoried but their side effects are not replayed or verified in v1.",
    "Exclusion constraints are reported but not generated against.",
    "CHECK constraints that cannot be parsed are enforced by PostgreSQL at load time, "
    "not pre-validated by pgproof.",
    "Profiles are computed from a sample, not from the full table.",
    "Application-level rules are only verified when declared in the spec file.",
]


@dataclass
class Report:
    verdict: str
    created_at: str
    seed: int
    database: str
    server_version: str
    schema_hash: str
    dataset_hash: str
    duration_seconds: float
    table_counts: dict[str, int]
    results: list[InvariantResult]
    warnings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=lambda: list(DEFAULT_LIMITATIONS))
    command: str = ""
    total_rows: int = 0

    def summary(self) -> dict[str, int]:
        return {
            "total": len(self.results),
            "passed": sum(1 for r in self.results if r.status == "pass"),
            "failed": sum(1 for r in self.results if r.status == "fail"),
            "error": sum(1 for r in self.results if r.status == "error"),
            "skipped": sum(1 for r in self.results if r.status == "skipped"),
            "violations": sum(r.violations for r in self.results),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": "pgproof",
            "format_version": 1,
            "verdict": self.verdict,
            "created_at": self.created_at,
            "seed": self.seed,
            "database": self.database,
            "server_version": self.server_version,
            "schema_hash": self.schema_hash,
            "dataset_hash": self.dataset_hash,
            "duration_seconds": self.duration_seconds,
            "total_rows": self.total_rows,
            "table_counts": self.table_counts,
            "summary": self.summary(),
            "invariants": [
                {
                    "id": result.id,
                    "kind": result.kind,
                    "table": result.table,
                    "description": result.description,
                    "status": result.status,
                    "violations": result.violations,
                    "sql": result.sql,
                    "error": result.error,
                    "note": result.note,
                    "sample": result.sample,
                }
                for result in self.results
            ],
            "warnings": self.warnings,
            "limitations": self.limitations,
            "reproduce": self.command,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False, sort_keys=False)

    def to_html(self) -> str:
        summary = self.summary()
        rows = "\n".join(_result_row(result) for result in self.results)
        failures = "\n".join(_failure_block(result) for result in self.results if result.violations)
        skipped = [result for result in self.results if result.status == "skipped"]
        skipped_block = "\n".join(
            f"<li><code>{html.escape(result.id)}</code> — {html.escape(result.note or 'not verified')}</li>"
            for result in skipped
        )
        warnings_block = "\n".join(f"<li>{html.escape(item)}</li>" for item in self.warnings)
        limitations_block = "\n".join(f"<li>{html.escape(item)}</li>" for item in self.limitations)
        verdict_class = "pass" if self.verdict == "PASS" else "fail"

        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pgproof verification report</title>
<style>
  :root {{
    --paper: #FAFAF7; --ink: #101418; --rule: #D9D9D2; --muted: #5B6470;
    --pass: #1E6B45; --fail: #A32020; --warn: #8A5A00;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--paper); color: var(--ink);
    font: 15px/1.55 "IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }}
  main {{ max-width: 1080px; margin: 0 auto; padding: 48px 24px 80px; }}
  h1 {{ font-size: 26px; letter-spacing: -0.01em; margin: 0 0 4px; }}
  h2 {{ font-size: 17px; margin: 40px 0 12px; }}
  .sub {{ color: var(--muted); font-size: 13px; }}
  .badge {{
    display: inline-block; font-weight: 600; font-size: 13px; letter-spacing: .08em;
    padding: 4px 10px; border: 1px solid currentColor; border-radius: 2px; margin-bottom: 16px;
  }}
  .badge.pass {{ color: var(--pass); }}
  .badge.fail {{ color: var(--fail); }}
  .grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1px; background: var(--rule); border: 1px solid var(--rule); margin: 24px 0;
  }}
  .cell {{ background: var(--paper); padding: 12px 14px; }}
  .cell .k {{ font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }}
  .cell .v {{ font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 14px; margin-top: 4px; word-break: break-all; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--rule); vertical-align: top; font-size: 13px; }}
  th {{ font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }}
  code, .mono {{ font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
  .status {{ font-weight: 600; }}
  .status.pass {{ color: var(--pass); }}
  .status.fail {{ color: var(--fail); }}
  .status.error {{ color: var(--fail); }}
  .status.skipped {{ color: var(--warn); }}
  ul {{ padding-left: 20px; }}
  li {{ margin: 4px 0; }}
  pre {{ background: #F1F1EC; border: 1px solid var(--rule); padding: 12px; overflow-x: auto; }}
  footer {{ margin-top: 48px; color: var(--muted); font-size: 12px; border-top: 1px solid var(--rule); padding-top: 12px; }}
</style>
</head>
<body>
<main>
  <div class="badge {verdict_class}">RESULT {html.escape(self.verdict)}</div>
  <h1>Synthetic data verification report</h1>
  <p class="sub">{html.escape(self.created_at)} · {self.total_rows:,} rows · {len(self.results)} invariants</p>

  <div class="grid">
    <div class="cell"><div class="k">Database</div><div class="v">{html.escape(self.database)}</div></div>
    <div class="cell"><div class="k">PostgreSQL</div><div class="v">{html.escape(self.server_version)}</div></div>
    <div class="cell"><div class="k">Seed</div><div class="v">{self.seed}</div></div>
    <div class="cell"><div class="k">Duration</div><div class="v">{self.duration_seconds}s</div></div>
    <div class="cell"><div class="k">Schema hash</div><div class="v">{html.escape(self.schema_hash[:32])}…</div></div>
    <div class="cell"><div class="k">Dataset hash</div><div class="v">{html.escape(self.dataset_hash[:32])}…</div></div>
  </div>

  <h2>Summary</h2>
  <table>
    <tr><th>Total</th><th>Passed</th><th>Failed</th><th>Errors</th><th>Skipped</th><th>Violations</th><th>Rows</th></tr>
    <tr>
      <td>{summary['total']}</td><td>{summary['passed']}</td><td>{summary['failed']}</td>
      <td>{summary['error']}</td><td>{summary['skipped']}</td><td>{summary['violations']}</td>
      <td>{self.total_rows:,}</td>
    </tr>
  </table>

  <h2>Invariants</h2>
  <table>
    <thead><tr><th>Status</th><th>Kind</th><th>Table</th><th>Description</th><th>Violations</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>

  {f'<h2>Failures</h2>{failures}' if failures else ''}

  <h2>Not verified</h2>
  <ul>{skipped_block or '<li>None.</li>'}</ul>

  <h2>Warnings</h2>
  <ul>{warnings_block or '<li>None.</li>'}</ul>

  <h2>Limitations</h2>
  <ul>{limitations_block}</ul>

  <h2>Reproduce</h2>
  <pre><code>{html.escape(self.command or 'pgproof run ...')}</code></pre>

  <footer>
    Generated by pgproof. Same seed, same rows: this report is reproducible.
    The dataset hash covers the seed and the per-table row counts.
  </footer>
</main>
</body>
</html>
"""


def _result_row(result: InvariantResult) -> str:
    status = html.escape(result.status)
    return (
        f"<tr><td class='status {status}'>{status.upper()}</td>"
        f"<td><code>{html.escape(result.kind)}</code></td>"
        f"<td><code>{html.escape(result.table)}</code></td>"
        f"<td>{html.escape(result.description)}"
        + (f"<br><span class='sub'>{html.escape(result.note)}</span>" if result.note else "")
        + (f"<br><span class='sub mono'>{html.escape(result.error or '')}</span>" if result.error else "")
        + f"</td><td>{result.violations}</td></tr>"
    )


def _failure_block(result: InvariantResult) -> str:
    sample = json.dumps(result.sample, indent=2, ensure_ascii=False) if result.sample else "[]"
    return (
        f"<h3><code>{html.escape(result.id)}</code></h3>"
        f"<p>{html.escape(result.description)} — <strong>{result.violations}</strong> violating rows.</p>"
        f"<pre><code>{html.escape(sample)}</code></pre>"
    )


def build_report(
    catalog: Catalog,
    verification: Verification,
    plan: Plan,
    table_counts: dict[str, int],
    warnings: list[str],
    command: str = "",
    dataset_hash: str | None = None,
) -> Report:
    schema_hash = hashlib.sha256(
        json.dumps(catalog.to_dict(), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    if dataset_hash is None:
        dataset_hash = hashlib.sha256(
            json.dumps({"seed": plan.seed, "counts": table_counts}, sort_keys=True).encode()
        ).hexdigest()

    return Report(
        verdict=verification.verdict,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        seed=plan.seed,
        database=verification.database,
        server_version=verification.server_version,
        schema_hash=schema_hash,
        dataset_hash=dataset_hash,
        duration_seconds=verification.duration_seconds,
        table_counts=table_counts,
        results=verification.results,
        warnings=warnings,
        command=command,
        total_rows=sum(table_counts.values()),
    )


def write_report(report: Report, directory: str | Path) -> tuple[Path, Path]:
    from pathlib import Path

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "report.json"
    html_path = target / "report.html"
    json_path.write_text(report.to_json(), encoding="utf-8")
    html_path.write_text(report.to_html(), encoding="utf-8")
    return json_path, html_path
