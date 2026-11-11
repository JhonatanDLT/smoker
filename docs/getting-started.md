# Getting started

## Requirements

- Python 3.11 or newer.
- PostgreSQL 14–17 (source and target).
- A database you are allowed to truncate. **Never point the target at production.**

## Install

Until the first public release:

```bash
git clone <repo> pgproof
cd pgproof
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pgproof version
```

## First run in three minutes

```bash
createdb pgproof_demo
psql -d pgproof_demo -f examples/saas-billing/schema.sql

export PGPROOF_SOURCE_DSN=postgresql:///pgproof_demo
export PGPROOF_TARGET_DSN=postgresql:///pgproof_demo

.venv/bin/pgproof run \
  --rows 200 \
  --seed 42 \
  --spec examples/saas-billing/plan.yml \
  --out proof
```

Expected output:

```
pgproof 0.9.6 · 10 tables · 2,000 rows · seed 42
  generate billing.accounts        200 rows
  ...
RESULT PASS · 92 passed · 0 failed · 0 errors · 1 skipped · 0 violations
report: proof/report.json
report: proof/report.html
report: proof/report.xml
```

Open `proof/report.html`.

## What just happened

1. **Introspect** read `pg_catalog`: tables, columns, constraints, enums, triggers.
2. **Plan** ordered tables parent-first and pre-allocated keys.
3. **Generate** wrote deterministic rows with `COPY`.
4. **Verify** ran every invariant as a SQL query and counted violations.
5. **Report** wrote JSON (canonical), HTML (human) and JUnit (CI).

The skipped item is the trigger: pgproof inventories triggers but does not replay
their side effects. That limitation is printed in the report.

## From a .sql dump (no live source)

If you only have a schema dump, pgproof can build the catalog without a
database connection:

```bash
pgproof load-dump db/schema.sql -o catalog.json
pgproof run \
  --schema-file db/schema.sql \
  --target-env PGPROOF_TARGET_DSN \
  --rows 5000 \
  --out proof
```

The loader extracts tables, columns, constraints, enums, domains, partitions,
functions, triggers and views. Relationships come from declared foreign keys;
when a dump omits them, pgproof infers them from `*_id` naming and from `JOIN`s
in views, marking them as `inferred`.

## Next steps

### Audit a schema without generating data

```bash
pgproof audit --dsn-env PGPROOF_SOURCE_DSN --out audit.md
```

Useful before a pilot or a migration. It reads metadata only.

### Reproduce the source's skew

```bash
pgproof profile --dsn-env PGPROOF_SOURCE_DSN --sample 10000 -o profile.json
pgproof run --profile profile.json --rows 5000 --out proof
```

### Repair a cross-table rule

```bash
pgproof run --spec examples/saas-billing/plan-repair.yml --out proof
```

The pipeline becomes generate → repair → verify. The report lists every repair.

### Use it in CI

See [CI integration](ci.md). The short version:

```yaml
- uses: ./
  with:
    source-dsn: ${{ secrets.STAGING_DB_URL }}
    target-dsn: ${{ secrets.CI_DB_URL }}
    spec: db/plan.yml
    fail-on: never   # shadow mode for two weeks, then switch to fail
```

## Safety checklist

- [ ] The target is not production.
- [ ] I have a backup of anything I care about in the target.
- [ ] The source user has read-only permissions.
- [ ] The target user can truncate the target tables.
- [ ] The spec names the schema explicitly (`public.invoices`, not `invoices`).

## Where to go next

- [CLI reference](cli-reference.md)
- [Spec reference](spec-reference.md)
- [Troubleshooting](troubleshooting.md)
