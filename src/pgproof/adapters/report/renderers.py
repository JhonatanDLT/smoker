"""Report renderers.

Adding a format (JUnit XML, SARIF, Markdown) means adding a class, not editing
the domain model. That is the open/closed principle applied to reporting.
"""

from __future__ import annotations

import html
import json

from ...core.report import Report
from ...core.verifier import InvariantResult


class JsonRenderer:
    extension = "json"

    def render(self, report: Report) -> str:
        return json.dumps(report.to_dict(), indent=2, ensure_ascii=False, sort_keys=False)


class HtmlRenderer:
    extension = "html"

    def render(self, report: Report) -> str:
        summary = report.summary()
        rows = "\n".join(_result_row(result) for result in report.results)
        failures = "\n".join(_failure_block(result) for result in report.results if result.violations)
        skipped = [result for result in report.results if result.status == "skipped"]
        skipped_block = "\n".join(
            f"<li><code>{html.escape(result.id)}</code> — "
            f"{html.escape(result.note or 'not verified')}</li>"
            for result in skipped
        )
        warnings_block = "\n".join(f"<li>{html.escape(item)}</li>" for item in report.warnings)
        limitations_block = "\n".join(
            f"<li>{html.escape(item)}</li>" for item in report.limitations
        )
        verdict_class = "pass" if report.verdict == "PASS" else "fail"
        repairs_block = _repairs_block(report)

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
  <div class="badge {verdict_class}">RESULT {html.escape(report.verdict)}</div>
  <h1>Synthetic data verification report</h1>
  <p class="sub">{html.escape(report.created_at)} · {report.total_rows:,} rows · {len(report.results)} invariants</p>

  <div class="grid">
    <div class="cell"><div class="k">Database</div><div class="v">{html.escape(report.database)}</div></div>
    <div class="cell"><div class="k">PostgreSQL</div><div class="v">{html.escape(report.server_version)}</div></div>
    <div class="cell"><div class="k">Seed</div><div class="v">{report.seed}</div></div>
    <div class="cell"><div class="k">Duration</div><div class="v">{report.duration_seconds}s</div></div>
    <div class="cell"><div class="k">Schema hash</div><div class="v">{html.escape(report.schema_hash[:32])}…</div></div>
    <div class="cell"><div class="k">Dataset hash</div><div class="v">{html.escape(report.dataset_hash[:32])}…</div></div>
  </div>

  <h2>Summary</h2>
  <table>
    <tr><th>Total</th><th>Passed</th><th>Failed</th><th>Errors</th><th>Skipped</th><th>Violations</th><th>Rows</th></tr>
    <tr>
      <td>{summary['total']}</td><td>{summary['passed']}</td><td>{summary['failed']}</td>
      <td>{summary['error']}</td><td>{summary['skipped']}</td><td>{summary['violations']}</td>
      <td>{report.total_rows:,}</td>
    </tr>
  </table>

  <h2>Invariants</h2>
  <table>
    <thead><tr><th>Status</th><th>Kind</th><th>Table</th><th>Description</th><th>Violations</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>

  {repairs_block}

  {f'<h2>Failures</h2>{failures}' if failures else ''}

  <h2>Not verified</h2>
  <ul>{skipped_block or '<li>None.</li>'}</ul>

  <h2>Warnings</h2>
  <ul>{warnings_block or '<li>None.</li>'}</ul>

  <h2>Limitations</h2>
  <ul>{limitations_block}</ul>

  <h2>Reproduce</h2>
  <pre><code>{html.escape(report.command or 'pgproof run ...')}</code></pre>

  <footer>
    Generated by pgproof. Same seed, same rows: this report is reproducible.
    The dataset hash covers the generated rows in table order.
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
        + (
            f"<br><span class='sub mono'>{html.escape(result.error or '')}</span>"
            if result.error
            else ""
        )
        + f"</td><td>{result.violations}</td></tr>"
    )


def _failure_block(result: InvariantResult) -> str:
    sample = json.dumps(result.sample, indent=2, ensure_ascii=False) if result.sample else "[]"
    return (
        f"<h3><code>{html.escape(result.id)}</code></h3>"
        f"<p>{html.escape(result.description)} — <strong>{result.violations}</strong> violating rows.</p>"
        f"<pre><code>{html.escape(sample)}</code></pre>"
    )


def _repairs_block(report: Report) -> str:
    repairs = report.repairs
    if not repairs or not repairs.actions:
        return ""
    rows = "\n".join(
        f"<tr><td><code>{html.escape(action.id)}</code></td>"
        f"<td>{action.pass_number}</td><td>{action.before}</td>"
        f"<td>{action.after}</td><td>{action.rows_affected}</td></tr>"
        for action in repairs.actions
    )
    return (
        "<h2>Repairs</h2>"
        f"<p>{repairs.applied} rows affected across {repairs.passes} pass(es); "
        f"residual violations: <strong>{repairs.residual}</strong>.</p>"
        "<table><thead><tr><th>Rule</th><th>Pass</th><th>Before</th>"
        f"<th>After</th><th>Rows</th></tr></thead><tbody>{rows}</tbody></table>"
    )
