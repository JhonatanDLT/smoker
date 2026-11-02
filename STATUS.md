# Status

**Date:** 2026-11-02
**Version:** 0.1.0
**State:** working v1 core, four fixture schemas, clean-architecture guardrails,
tested end to end against PostgreSQL 16.

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
| Composite unique keys | ✅ |
| Scope-aware foreign keys (`tenant_id`) | ✅ |
| Unique values after `varchar(n)` truncation | ✅ |
| `COPY` writer with `INSERT` fallback | ✅ |
| Independent verifier with violation samples | ✅ |
| Structured load errors (table, constraint, row, values, SQLSTATE) | ✅ |
| JSON + HTML report with limitations | ✅ |
| Dataset SHA-256 (reproducibility) | ✅ |
| CLI: `introspect`, `plan`, `generate`, `verify`, `run` | ✅ |
| Clean architecture: ports, use cases, adapters | ✅ |
| Architecture guards in the test suite | ✅ |
| Ruff clean | ✅ |

## Fixtures

| Fixture | Invariants | Result |
|---|---|---|
| `saas-billing` | 93 | PASS |
| `ecommerce` | 92 | PASS |
| `multi-tenant-crm` | 79 | PASS |
| `analytics` | 74 | PASS |

## Measured

- 10-table billing schema, 180,000 rows generated and verified in **5.04 s**.
- Same seed produces the same dataset hash every run.
- **24 tests passing**: 13 unit, 8 integration, 3 architecture.
- Fixture runner creates one database per schema and writes a report for each.

## How to run it

```bash
cd pgproof
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

make demo        # billing demo, opens proof/report.html
make fixtures    # ecommerce, multi-tenant CRM, analytics
make test
PGPROOF_TEST_DSN=postgresql:///pgproof_demo .venv/bin/pytest -m integration
```

## Architecture

```
cli ──► application ──► core (domain + ports)
                         ▲
                    adapters (Postgres, YAML, report)
```

- `core` has no third-party imports beyond the standard library.
- `application` depends on ports, never on adapters.
- `tests/test_architecture.py` fails the build if the rule is broken.
- Adding SQL Server = one adapter. Adding JUnit XML = one renderer.

## Known gaps (v1, by design)

- Trigger side effects are inventoried but not replayed.
- Exclusion constraints are not generated against.
- Cross-table business rules are verified only if declared; they are not repaired.
- Scope detection covers shared columns that are foreign keys in both tables.
- Partitioned tables, RLS policies, arrays and `bytea` are partial.
- SQL Server, MySQL and static code analysis are roadmap items.

See [`docs/limitations.md`](docs/limitations.md).

## Next tasks

1. JUnit XML renderer and a `--fail-on` policy (GitHub Action deliberately
   postponed; keep the renderer ready for it).
2. Distribution-aware generation from profiles.
3. Repair loop for declared application rules.
4. Partitioned table routing.
5. First design-partner pilot with the "two weeks or you do not pay" offer.

## Decision rules

- **Accelerate:** 3 payments by day 60 and activation ≥ 50%.
- **Pivot the wedge:** by day 90 with fewer than 2 payments, move to migration
  validation as a service for 30 days.
- **Kill:** by day 120 with fewer than 3 payments, fewer than 5 pilots, or CAC
  above 3 months of MRR.
