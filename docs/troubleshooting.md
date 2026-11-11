# Troubleshooting

## Connection problems

```
psycopg.OperationalError: connection failed
```

- Check the DSN with `psql "$SMOKER_SOURCE_DSN"`.
- If the variable is not set, smoker says `pass --dsn or set ...`.
- For Supabase, Neon or RDS, add `?sslmode=require` or `sslmode=verify-full`.
- In Docker, `localhost` is the container, not the host. Use the service name.

## Load errors

```
load failed in billing.invoice_lines
  reason:     new row for relation "invoice_lines" violates check constraint "invoice_lines_amount_matches"
  constraint: invoice_lines_amount_matches
  sqlstate:   23514
  row:        1
  values:     id=1, quantity=98, unit_price=709.50, amount=729.22
  detail:     Failing row contains (1, 98, 709.50, 729.22).
```

Read it in this order:

1. **Table** — where the row failed.
2. **Constraint** — what rule broke.
3. **Row and values** — the exact row. smoker replays the deterministic
   generator to recover it.
4. **SQLSTATE** — `23514` check, `23505` unique, `23503` foreign key.

### Check violation

The generator could not parse the predicate. Options:

- Add a `repair:` that fixes the column.
- Rewrite the check in a form the parser understands (`col > 0`, `col IN (...)`,
  `amount = quantity * unit_price`, `ends_at > starts_at`).
- Pre-compute the value with a derived rule.

### Unique violation

- The constraint is composite and does not contain a text column.
- The key uses a type the generator does not treat as unique (for example a
  domain over `varchar`).
- Two tables share a sequence.

Check that the primary key is detected:

```bash
smoker introspect -o catalog.json
jq '.tables[] | select(.name=="your_table") | .primary_key' catalog.json
```

### Foreign key violation

- The parent table has no primary or unique key.
- The foreign key points at a unique key with a different column order.
- A table is partitioned and its partitions were treated as separate tables.

## Generation errors

```
generation failed: public.users row 7: could not satisfy local rules after 50 attempts (age violates < 18)
```

The rules are contradictory or too narrow. Examples:

- `age > 18` and `age < 18`.
- A unique column of length 2 with more than 100 rows.
- A check that references a column the generator does not populate.

## Spec errors

```
invalid spec: invariant #2 is missing `sql`
```

- Every entry needs `sql`.
- YAML indentation matters; use two spaces.
- Quote strings that start with `*` or `&`.
- Validate with `python -c "import yaml;yaml.safe_load(open('plan.yml'))"`.

## Repair errors

```
repair invoice_total_matches_lines failed: column "total" does not exist
```

- Use schema-qualified names.
- The repair runs in the target database, not the source.
- A failing repair rolls back the whole repair transaction.
- If a repair does not converge, the report shows `residual > 0` and the run
  fails under `--fail-on fail`. Increase `--max-passes` only if the repair is
  genuinely progressive.

## Permission errors

```
permission denied for table invoices
```

The target role needs `TRUNCATE`, `INSERT`, `UPDATE` and `USAGE` on sequences.
The source role needs `SELECT` on the tables and `USAGE` on the schema.

## Numeric overflow

```
numeric field overflow
```

- A profile bucket produced a value larger than `numeric(p,s)`.
- A derived rule multiplied two large numbers.
- Reduce `--rows` or add a tighter check; the generator respects the scale of
  the column but not the precision of every intermediate operation.

## Triggers

Triggers are inventoried but their effects are not replayed. The report marks
them as `skipped`. If a trigger writes to an audit table, that table becomes part
of the dataset only if it is in the plan and you generate it explicitly.

## Source and target are the same

The CLI warns. The target will be truncated. Use it only for demos or an
ephemeral CI database. The GitHub Action refuses unless `allow-same-db: true`.

## Reproducibility mismatch

Two runs with the same seed should produce the same `dataset_hash`.

If they differ:

- The schema changed between runs.
- The profile changed.
- A repair changed a different number of rows.
- The source and target are different databases with different schemas.

## Nothing is verified

If `summary.total` is 0, the introspection found no tables. Check the schema name
and the connection user's permissions.

## Getting help

Collect:

```bash
smoker version
smoker introspect -o catalog.json
cat proof/report.json | jq '.summary, .warnings'
```

Do not include DSNs or real data in a bug report.
