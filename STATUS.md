# Status

**Date:** 2026-11-02
**Version:** 0.1.0
**State:** working v1 core, tested end to end against PostgreSQL 16.

---

## What works

| Capability | State |
|---|---|
| Catalog introspection (`pg_class`, `pg_constraint`, `pg_trigger`, `pg_proc`) | ✅ |
| Invariants from NOT NULL, UNIQUE, PK, FK, CHECK, enums | ✅ |
| Application rules from YAML spec | ✅ |
| Deterministic generation (seed per table) | ✅ |
| Pre-allocated keys (foreign-key cycles) | ✅ |
| Local rules: ranges, `IN`, `a = b * c`, `ends_at > starts_at` | ✅ |
| Unique values after `varchar(n)` truncation | ✅ |
| `COPY` writer with `INSERT` fallback | ✅ |
| Independent verifier with violation samples | ✅ |
| JSON + HTML report with limitations | ✅ |
| Dataset SHA-256 (reproducibility) | ✅ |
| CLI: `introspect`, `plan`, `generate`, `verify`, `run` | ✅ |
| Exit code 1 on `FAIL` (CI-ready) | ✅ |

## Measured

- 10-table demo schema, 180,000 rows generated and verified in **5.04 s**.
- 92 invariants passed, 0 violations, 1 skipped (trigger).
- Two runs with the same seed produce the same dataset hash:
  `bc4ba84aa1c655cf1543469b1aa6e7c6ca518cd60fff7c321f592d8e0fe3a0db`.
- Unit tests: 16 passing.
- Integration tests: 2 passing against a live PostgreSQL 16.

## How to run it

```bash
cd pgproof
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

createdb pgproof_demo
psql -d pgproof_demo -f examples/saas-billing/schema.sql

export PGPROOF_SOURCE_DSN=postgresql:///pgproof_demo
export PGPROOF_TARGET_DSN=postgresql:///pgproof_demo

.venv/bin/pgproof run \
  --rows 200 \
  --seed 42 \
  --spec examples/saas-billing/plan.yml \
  --out proof

open proof/report.html
```

Or simply:

```bash
make demo
```

## Known gaps (v1, by design)

- Trigger side effects are inventoried but not replayed.
- Exclusion constraints are not generated against.
- Cross-table business rules are verified only if declared; they are not repaired.
- Partitioned tables, RLS policies, arrays and `bytea` are partial.
- SQL Server, MySQL and static code analysis are roadmap items.

See [`docs/limitations.md`](docs/limitations.md).

## Next five tasks

1. GitHub Action + JUnit XML output.
2. Better failure messages: table, constraint, row, offending SQL.
3. Three public fixture schemas (e-commerce, multi-tenant CRM, analytics).
4. `--fail-on` policy and `--strict` mode.
5. First design-partner pilot using the "two weeks or you do not pay" offer.

## Decision rules

- **Accelerate:** 3 payments by day 60 and activation ≥ 50%.
- **Pivot the wedge:** by day 90 with fewer than 2 payments, move to migration
  validation as a service for 30 days.
- **Kill:** by day 120 with fewer than 3 payments, fewer than 5 pilots, or CAC
  above 3 months of MRR.
