# Spec reference

The spec is a YAML file with application-level rules that the database cannot
express as a constraint. Catalog invariants are always verified; the spec adds
the rest.

```yaml
invariants:
  - id: invoice_total_matches_lines
    table: billing.invoices
    description: invoice total equals the sum of its lines
    sql: |
      SELECT i.id
      FROM billing.invoices i
      LEFT JOIN (
        SELECT invoice_id, SUM(amount) AS line_total
        FROM billing.invoice_lines
        GROUP BY invoice_id
      ) l ON l.invoice_id = i.id
      WHERE i.total <> COALESCE(l.line_total, 0)
    repair: |
      UPDATE billing.invoices i
      SET total = COALESCE((
        SELECT SUM(l.amount)
        FROM billing.invoice_lines l
        WHERE l.invoice_id = i.id
      ), 0)
```

## Fields

| Field | Required | Description |
|---|---|---|
| `id` | No | Stable identifier. Defaults to `app_rule:<index>`. |
| `table` | No | Main table, for reporting. Use `schema.table`. |
| `description` | No | Human sentence shown in the report. |
| `columns` | No | Columns involved, for reporting. |
| `sql` | **Yes** | Query that returns **violating rows**, not a boolean. |
| `repair` | No | SQL statement that reduces the violations. |

## Rules for `sql`

- It must return one row per violation. `SELECT 1` is valid if you only need the
  count.
- It runs as `SELECT count(*) FROM (<sql>) violations`, so it must be a valid
  subquery.
- Use schema-qualified names. `invoices` is ambiguous when the search path
  changes.
- Keep it read-only. Repairs belong in `repair`.
- Prefer explicit `JOIN`s over correlated subqueries on large tables.

### Row-level rule

```yaml
- id: positive_amount
  table: billing.invoice_lines
  description: amount is positive
  sql: |
    SELECT id FROM billing.invoice_lines WHERE amount <= 0
```

### Cross-table rule

```yaml
- id: payment_not_above_invoice
  table: billing.payments
  description: a payment never exceeds its invoice total
  sql: |
    SELECT p.id
    FROM billing.payments p
    JOIN billing.invoices i ON i.id = p.invoice_id
    WHERE p.amount > i.total
```

### Existence rule

```yaml
- id: every_account_has_a_user
  table: billing.accounts
  description: every account has at least one user
  sql: |
    SELECT a.id
    FROM billing.accounts a
    WHERE NOT EXISTS (SELECT 1 FROM billing.users u WHERE u.account_id = a.id)
```

## Rules for `repair`

- It must be deterministic. No `now()`, no random values.
- It should be idempotent: running it twice must not change the result.
- It runs in the same transaction as the other repairs, then commits.
- It runs at most `--max-passes` times (default 3), stopping at a fixed point.
- If it raises, the whole repair transaction rolls back and the run exits `2`.
- It must not touch tables outside the target database.

### Repair an aggregate

```yaml
repair: |
  UPDATE billing.invoices i
  SET total = COALESCE((
    SELECT SUM(l.amount) FROM billing.invoice_lines l WHERE l.invoice_id = i.id
  ), 0)
```

### Repair a conditional column

```yaml
repair: |
  UPDATE billing.subscriptions
  SET ends_at = started_at + interval '1 day'
  WHERE ends_at IS NOT NULL AND ends_at <= started_at
```

### What not to repair

- Do not delete rows to satisfy a rule. The dataset must remain useful.
- Do not invent values that violate other constraints. The verifier runs after
  the repair and will catch it.
- Do not hide a rule behind a repair that always sets the same constant. The
  report will show zero violations but the data becomes useless.

## How the pipeline uses the spec

```
invariants_from_catalog()  ─┐
                            ├─► merge ─► verify
spec.invariants             ─┘
                              │
                              └─► repair (only entries with `repair:`)
```

1. Catalog invariants and spec rules are merged.
2. `repair` statements run for rules that currently have violations.
3. The full set is verified again.
4. The report shows pass/fail per rule and a Repairs section.

## Validation

The loader rejects:

- A non-mapping entry.
- An entry without `sql`.
- Invalid YAML.

Unknown fields are ignored today. Treat the spec as code: review it in a pull
request and commit it next to the schema.

## Roadmap

Declared correlations are planned for v0.3:

```yaml
correlations:
  - columns: [country, city]
    strategy: lookup
```

They are documented but not implemented yet. Do not rely on them.
