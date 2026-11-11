# Status

**Date:** 2026-11-11
**Version:** 0.9.1
**State:** working v1 plus CI output, repair loop, distribution-aware
generation, partitioned tables, offline schema dumps, segmentation rules, a
dependency-safe creation order, SQL export, exclusion-aware generation, a
dialect layer and a source-first SQL Server adapter (experimental, verified
against SQL Server 2022 Developer). Tested end to end against PostgreSQL 16 and
SQL Server 2022.

---

## What works

| Capability | State |
|---|---|
| Catalog introspection (`pg_class`, `pg_constraint`, `pg_trigger`, `pg_proc`) | ✅ |
| **Offline schema dump loader** (`pgproof load-dump`, `--schema-file`) | ✅ |
| Functions, triggers, views and partitions from a dump | ✅ |
| `LIKE`, `INHERITS`, `ATTACH PARTITION`, CTEs and dynamic SQL | ✅ |
| **Inferred foreign keys** from `*_id` naming and view JOINs | ✅ |
| **Provenance** on tables, constraints, FKs, functions, triggers and raw objects | ✅ |
| **Segmentation extraction** (`RAISE EXCEPTION`, `NEW.col :=`, view filters) | ✅ report-only |
| **Constraint space** defined only by table constraints; rules never change it | ✅ |
| **SQL INSERT export** (`pgproof generate --sql`) from a dump, no database | ✅ |
| **Rules in the report** (`rules[]`, HTML table) and per-invariant provenance | ✅ |
| **View filters** classified as aggregate candidates (never applied as checks) | ✅ |
| **Exclusion constraints** parsed, verified and generated against | ✅ |
| **Dependency-safe creation order** (`pgproof order`) | ✅ |
| **Dialect layer** (`SqlDialect`, `Capabilities`, registry) | ✅ |
| **`DatabaseAdapter` aggregate** (dialect + connection factories) | ✅ |
| **SQL Server dialect, introspection, query runner and writer** (experimental, verified) | ✅ |
| **T-SQL dump loader and SQL Server profiler** | ❌ pending |
| **Dialect-aware invariant SQL, SQL export and report label** | ✅ |
| **`--dialect` extension point** (SQL Server adapter pending) | ✅ |
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
- Dump hardening: `LIKE ... INCLUDING ALL`, `INHERITS`, `ATTACH PARTITION`,
  CTE joins, dynamic SQL and `IF`/`CASE` conditions are extracted and covered by
  tests.
- Segmentation extraction: `IF NEW.quantity <= 0 THEN RAISE EXCEPTION` and
  `NEW.amount := NEW.quantity * NEW.unit_price` are reported as `guard` and
  `derived` candidates with provenance. They never become table checks and do
  not change the generation space; the trigger itself is still not replayed.
- **SQL export:** a dump with two tables generated 7 deterministic rows into a
  `.sql` file; `psql -f` loaded them with zero orphan foreign keys.
- Creation order: `pgproof order` lists extensions, enums, types, domains,
  sequences, tables (FK order), functions, indexes, triggers, views, rules and
  policies, with `depends_on` per step.
- `load-dump` and `plan` now include `creation_order` in their JSON output.
- **Exclusion constraints:** a dump with `EXCLUDE USING btree (room WITH =)`,
  `EXCLUDE USING gist (during WITH &&)` and a combined predicate exclusion
  generated 20 rows per table and loaded into PostgreSQL without a violation;
  the report passed.
- **Dialect layer:** PostgreSQL invariant SQL moved out of the core; the
  dialect and architecture contract tests enforce the boundary.
- **SQL Server:** T-SQL dialect, `sys.*` introspection, query runner and writer
  are implemented and registered as `--dialect sqlserver`; integration tests
  run against SQL Server 2022 Developer.
- **141 tests passing** with both `PGPROOF_TEST_DSN` and `PGPROOF_MSSQL_DSN`:
  PostgreSQL 16 plus SQL Server 2022 Developer in a container.
- SQL Server integration covers source introspection, verification, generation,
  identity/computed/rowversion, `varbinary`/`uniqueidentifier`/`datetime2` and
  SQL script export applied to the server.
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

- Dump loading: `CREATE RULE`, event triggers and policies are stored raw but
  not applied; `CREATE TABLE ... AS SELECT` is stored raw.
- PL/pgSQL bodies are stored and scanned for dynamic SQL and conditions.
  `IF ... RAISE` and `NEW.col :=` become segmentation candidates with
  provenance; they never become checks and never change the generation space.
- Trigger effects are inventoried, not replayed.
- Segmentation extraction runs on `.sql` dumps; live introspection still
  inventories functions and triggers without deriving rules from them.
- View filters are report-only aggregate candidates; they never constrain the
  base table or other candidates.
- Inferred foreign keys are heuristic; check the `inferred` flag.
- Exclusion constraints are parsed, verified and generated against. Supported
  operators are `=`, `<>`, `<`, `<=`, `>`, `>=`, `&&`, `@>` and `<@`; other
  operators fail generation loudly. Partitioned tables still need manual review
  for `ctid`-based verification.
- SQL export emits INSERT statements; the target schema must already exist.
- Creation-order dependencies for views come from the parsed query; dynamic SQL
  inside functions is not expanded into dependencies.
- Distributions are per column; correlations are not modelled.
- No parallel verification yet.
- Cross-table rules need an explicit `repair:` declaration.
- SQL Server is implemented source-first (experimental) and was verified against
  SQL Server 2022 Developer. T-SQL dump loading, profiling and partition-tree
  mapping are pending; it is not yet production-ready.
- MySQL and Oracle adapters are not implemented yet; the dialect contract and
  registry are ready for them.
- Static code analysis is a roadmap item.

See [`docs/limitations.md`](docs/limitations.md).

## Next tasks

1. Add the SQL Server integration suite to CI (container + `PGPROOF_MSSQL_DSN`)
   and keep it green.
2. T-SQL dump loader (`GO`, `[brackets]`, `IDENTITY`, `MERGE`) and SQL Server
   profiler.
3. Column correlations and parallel verification.
4. Publish to PyPI and the GitHub Marketplace; first design-partner pilot.

## Decision rules

- **Accelerate:** 3 payments by day 60 and activation ≥ 50%.
- **Pivot the wedge:** by day 90 with fewer than 2 payments, move to migration
  validation as a service for 30 days.
- **Kill:** by day 120 with fewer than 3 payments, fewer than 5 pilots, or CAC
  above 3 months of MRR.
