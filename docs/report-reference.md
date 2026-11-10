# Report reference

Every run writes a report. The JSON is canonical; HTML and JUnit are renderings.

```
proof/
├── report.json   canonical
├── report.html   human-readable
├── report.xml    JUnit for CI
├── catalog.json  schema snapshot
└── plan.json     generation plan
```

## JSON — `format_version: 2`

| Field | Type | Description |
|---|---|---|
| `tool` | string | `pgproof` |
| `format_version` | int | `2` |
| `verdict` | string | `PASS` or `FAIL` |
| `created_at` | ISO-8601 | UTC timestamp |
| `seed` | int | Deterministic seed |
| `database` | string | Target database |
| `server_version` | string | PostgreSQL version |
| `schema_hash` | sha256 | Hash of the catalog |
| `dataset_hash` | sha256 | Hash of the generated rows (or of the target after repairs) |
| `duration_seconds` | float | Verification time |
| `total_rows` | int | Sum of table counts |
| `table_counts` | object | Rows per table |
| `summary` | object | Counts by status |
| `invariants` | array | One entry per invariant |
| `warnings` | array | Structural warnings |
| `limitations` | array | What was not verified |
| `reproduce` | string | Command that produced the report |
| `repairs` | object | Repairs applied |
| `rules` | array | Rules extracted from triggers and functions |

### `summary`

```json
{
  "total": 93,
  "passed": 92,
  "failed": 0,
  "error": 0,
  "skipped": 1,
  "violations": 0
}
```

### `invariants[]`

| Field | Description |
|---|---|
| `id` | Stable identifier |
| `kind` | `not_null`, `unique`, `foreign_key`, `check`, `enum`, `app_rule`, `trigger` |
| `table` | `schema.table` |
| `description` | Human sentence |
| `status` | `pass`, `fail`, `error`, `skipped` |
| `violations` | Number of violating rows |
| `sql` | Query used to find them |
| `error` | Database error, when `status = error` |
| `note` | Why it was skipped, or a caveat |
| `provenance` | Where the rule came from (`CREATE TABLE …`, trigger name, inferred key) |
| `sample` | Up to `--sample` offending rows |

### `repairs`

```json
{
  "actions": [
    {
      "id": "invoice_total_matches_lines",
      "table": "billing.invoices",
      "pass_number": 1,
      "before": 50,
      "after": 0,
      "rows_affected": 50
    }
  ],
  "passes": 1,
  "residual": 0,
  "applied": 50,
  "improved": true
}
```

`residual` is the number of violations left in repairable rules after the loop.
It is `0` when the repair converged.

### `rules`

Rules extracted from trigger and function bodies. They are not declared
constraints; they are code-derived candidates.

```json
[
  {
    "id": "rule:billing.invoice_lines:1",
    "kind": "derived",
    "table": "billing.invoice_lines",
    "expression": "amount = quantity * unit_price",
    "columns": ["amount", "quantity", "unit_price"],
    "confidence": 0.9,
    "applied": true,
    "scope": "row",
    "provenance": {
      "kind": "trigger",
      "source": "invoice_lines_amount",
      "detail": "billing.set_line_amount"
    },
    "note": "NEW.amount := quantity * unit_price"
  }
]
```

- `kind` is `check` (from `IF … RAISE`), `derived` (from `NEW.col := …`)
  or `filter` (from a view `WHERE`).
- `confidence` is `0.0`–`1.0`; only `applied = true` rules are enforced as
  checks and included in generation.
- `scope` is `row` for trigger/function rules and `aggregate` for view
  filters. Aggregate rules are report-only: they scope a view, never the base
  table, and do not affect other candidates.
- `applied = false` means the rule is reported but needs review: compound
  conditions, unknown functions, `now()`, `random()`, self-referential
  assignments or a view filter.
- `provenance.kind` is `trigger`, `function` or `view`.

## Hashes

- **`schema_hash`** covers the catalog: tables, columns, constraints, enums.
  It changes when the schema changes.
- **`dataset_hash`** covers the generated rows in table order. When repairs run,
  it is recomputed from the target so it describes the final data.

To prove reproducibility:

```bash
pgproof run --seed 42 --out /tmp/a
pgproof run --seed 42 --out /tmp/b
jq -r .dataset_hash /tmp/a/report.json
jq -r .dataset_hash /tmp/b/report.json
```

Both hashes must match. If they do not, something in the schema or the source
profile changed.

## HTML

The HTML report is a single self-contained file: no external fonts, no scripts.
It contains the verdict, the summary, every invariant, failures with samples,
repairs, extracted rules, warnings and limitations.

## JUnit XML

```
<testsuites tests="93" failures="0" errors="0" skipped="1">
  <testsuite ...>
    <testcase classname="check" name="check:billing.invoices:abc123">
      <failure message="...">SQL and sample</failure>
    </testcase>
    <testcase classname="trigger" name="trigger:billing.accounts:def456">
      <skipped message="trigger effects are not verified in v1"/>
    </testcase>
  </testsuite>
</testsuites>
```

- `failures` counts invariant failures.
- `errors` counts verification errors (the query could not run).
- `skipped` counts invariants that were not verified.
- Passed test cases have no child element.

## Exit codes and policy

The report is always written. The process exit code depends on `--fail-on`:

| Policy | Exit 1 when |
|---|---|
| `never` | Never |
| `error` | `summary.error > 0` |
| `fail` | Verdict is `FAIL` |

Load and configuration errors exit `2` and may not write a report.
