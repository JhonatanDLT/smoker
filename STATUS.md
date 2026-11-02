# Status

**Date:** 2026-11-02
**Version:** 0.2.0
**State:** working v1 plus CI output, repair loop and distribution-aware
generation. Tested end to end against PostgreSQL 16.

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
| JSON + HTML + **JUnit XML** report | ✅ |
| `--fail-on never/error/fail` (shadow mode) | ✅ |
| **Repair loop** with fixed-point limit and residual reporting | ✅ |
| **Distribution profiling** (`pgproof profile`) and profile-aware generation | ✅ |
| Dataset SHA-256 (recomputed after repairs) | ✅ |
| CLI: `introspect`, `profile`, `plan`, `generate`, `verify`, `run` | ✅ |
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
- Repair: 50 invoices repaired in one pass, residual 0, PASS.
- Distribution: source skew 80/20 reproduced at **81/19** in the target, PASS.
- **45 tests passing** (unit, integration and architecture guards).
- Same seed produces the same dataset hash every run.

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

Repair demo:

```bash
PGPROOF_SOURCE_DSN=postgresql:///pgproof_demo \
PGPROOF_TARGET_DSN=postgresql:///pgproof_demo \
  .venv/bin/pgproof run --rows 50 \
  --spec examples/saas-billing/plan-repair.yml --out proof
```

Distribution demo:

```bash
pgproof profile --dsn-env PGPROOF_SOURCE_DSN --sample 10000 -o profile.json
pgproof run --profile profile.json --rows 5000 --out proof
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
- Adding SQL Server = one adapter. Adding SARIF = one renderer.

## Known gaps

- Trigger side effects are inventoried but not replayed.
- Exclusion constraints are not generated against.
- Distributions are per column; correlations are not modelled.
- Date histograms are not used.
- Cross-table rules need an explicit `repair:` declaration.
- SQL Server, MySQL and static code analysis are roadmap items.

See [`docs/limitations.md`](docs/limitations.md).

## Next tasks

1. GitHub Action on top of the JUnit renderer (deliberately postponed).
2. `TABLESAMPLE` and date histograms for profiles.
3. Column correlations (country ↔ city, price ↔ category).
4. Partitioned table routing.
5. First design-partner pilot with the "two weeks or you do not pay" offer.

## Decision rules

- **Accelerate:** 3 payments by day 60 and activation ≥ 50%.
- **Pivot the wedge:** by day 90 with fewer than 2 payments, move to migration
  validation as a service for 30 days.
- **Kill:** by day 120 with fewer than 3 payments, fewer than 5 pilots, or CAC
  above 3 months of MRR.
