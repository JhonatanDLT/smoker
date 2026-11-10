# Status

**Date:** 2026-11-10
**Version:** 0.4.0
**State:** working v1 plus CI output, repair loop, distribution-aware
generation, partitioned tables and offline schema dumps. Tested end to end
against PostgreSQL 16.

---

## What works

| Capability | State |
|---|---|
| Catalog introspection (`pg_class`, `pg_constraint`, `pg_trigger`, `pg_proc`) | ✅ |
| **Offline schema dump loader** (`pgproof load-dump`, `--schema-file`) | ✅ |
| Functions, triggers, views and partitions from a dump | ✅ |
| **Inferred foreign keys** from `*_id` naming and view JOINs | ✅ |
| Invariants from NOT NULL, UNIQUE, PK, FK, CHECK, enums | ✅ |
| Application rules from YAML spec | ✅ |
| Deterministic generation (seed per table) | ✅ |
| Pre-allocated keys (foreign-key cycles) | ✅ |
| Partitioned tables (range, list and hash, single-column keys) | ✅ |
| Multi-column partition keys and sub-partitioning | ✅ |
| Schema include/exclude filters (`--schema`, `--include`, `--exclude`) | ✅ |
| Trigger functions deduplicated by OID | ✅ |
| Deep foreign-key chains (iterative SCC, 5,000 tables tested) | ✅ |
| Batched `TRUNCATE` (100 tables per statement) | ✅ |
| Local rules: ranges, `IN`, `a = b * c`, `ends_at > starts_at` | ✅ |
| Composite unique keys | ✅ |
| Scope-aware foreign keys (`tenant_id`) | ✅ |
| Unique values after `varchar(n)` truncation | ✅ |
| `COPY` writer with `INSERT` fallback | ✅ |
| Independent verifier with violation samples | ✅ |
| Structured load errors (table, constraint, row, values, SQLSTATE) | ✅ |
| JSON + HTML + **JUnit XML** report | ✅ |
| `--fail-on never/error/fail` (shadow mode) | ✅ |
| **GitHub Action** with annotations and job summary | ✅ |
| **Schema readiness audit** (`pgproof audit`) | ✅ |
| **Repair loop** with fixed-point limit and residual reporting | ✅ |
| **Distribution profiling** (`pgproof profile`) and profile-aware generation | ✅ |
| **TABLESAMPLE** sampling and date/timestamp histograms | ✅ |
| Dataset SHA-256 (recomputed after repairs) | ✅ |
| CLI: `introspect`, `load-dump`, `profile`, `plan`, `generate`, `verify`, `run` | ✅ |
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
| `partitioned-events` | 29 | PASS |

## Measured

- 10-table billing schema, 180,000 rows generated and verified in **5.04 s**.
- 300 tables × 1,000 rows (300k rows, 2,399 invariants): **6.9 s**, 79 MB.
- 1,200 tables × 1,000 rows (1.2M rows, 9,599 invariants): **28 s**, 173 MB.
- 5,000-table reverse-ordered FK chain: plan succeeds with the iterative SCC.
- Partitioned fixture: 60 rows routed across 12 range partitions and 4 hash
  partitions; multi-column ranges split tenants 1–99 / 100–199; a
  sub-partitioned table routes rows by month and level.
- Introspection reports 5 parents, not 25 relations.
- 500 triggers sharing one function produce **one** function body, not 500.
- Profile: a 60k-row table sampled with `TABLESAMPLE SYSTEM (0.83%)` (500
  rows), producing a 19-bucket date histogram; generation with that profile
  passed.
- Repair: 50 invoices repaired in one pass, residual 0, PASS.
- Distribution: source skew 80/20 reproduced at **81/19** in the target, PASS.
- **Dump-only generation:** 10-table billing schema loaded from `.sql`, 500
  rows generated, 93 invariants PASS; the partitioned dump generated 300 rows
  and 29 invariants PASS.
- **91 tests passing** (unit, integration and architecture guards).
- Same seed produces the same dataset hash every run.

## Documentation

The full set lives in [`docs/`](docs/README.md): getting started, CLI, spec,
report formats, CI, security, troubleshooting, architecture, limitations, FAQ,
contributing and the pilot playbook.

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

- Dump loading: PL/pgSQL bodies are stored but not parsed; `CREATE RULE`,
  event triggers, policies, `LIKE`, `INHERITS` and `ATTACH PARTITION` are not
  applied.
- Inferred foreign keys are heuristic; check the `inferred` flag.
- Trigger side effects are inventoried but not replayed.
- Stored procedures and rules are not analyzed.
- Exclusion constraints are not generated against.
- Distributions are per column; correlations are not modelled.
- No parallel verification yet.
- Cross-table rules need an explicit `repair:` declaration.
- SQL Server, MySQL and static code analysis are roadmap items.

See [`docs/limitations.md`](docs/limitations.md).

## Next tasks

1. Harden the dump loader: `LIKE`, `INHERITS`, `ATTACH PARTITION`, rules and
   policies.
2. Extract simple trigger/function rules (`RAISE EXCEPTION`, `NEW.col :=`).
3. Column correlations and parallel verification.
4. SQL Server adapter.
5. Publish to PyPI and the GitHub Marketplace; first design-partner pilot.

## Decision rules

- **Accelerate:** 3 payments by day 60 and activation ≥ 50%.
- **Pivot the wedge:** by day 90 with fewer than 2 payments, move to migration
  validation as a service for 30 days.
- **Kill:** by day 120 with fewer than 3 payments, fewer than 5 pilots, or CAC
  above 3 months of MRR.
