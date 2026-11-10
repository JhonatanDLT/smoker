# Changelog

All notable changes to pgproof. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses semantic
versioning.

## [0.5.0] — 2026-11-10

### Added

- **Rule extraction from triggers and functions**:
  - `IF <condition> THEN RAISE EXCEPTION|ERROR|WARNING` → invariant candidate
    `NOT <condition>`.
  - `NEW.<col> := <expression>` → derived rule `<col> = <expression>`.
  - Every rule has `confidence`, `applied`, `columns`, `note` and
    `provenance` (`trigger` or `function`).
- **Simple rules become real checks**: comparisons, `IN`, `IS NOT NULL`,
  `BETWEEN`, `LIKE`, length limits and column-to-column assignments are added to
  `table.checks`, so generation enforces them and verification reports them.
- **Rules in the report**: `rules[]` in JSON, an "Extracted rules" table in HTML,
  and `provenance` on each invariant result.
- End-to-end test: a dump with a trigger rule is generated, loaded and verified.

### Changed

- Derived-rule parsing no longer lets a bare copy rule (`a = b`) override an
  arithmetic rule (`a = b * c`).

### Measured

- 102 tests passing.

## [0.4.1] — 2026-11-10

### Added

- **`LIKE ... INCLUDING ...`** expansion: columns, defaults, constraints and
  indexes are copied from the source table.
- **`INHERITS`** expansion: columns and CHECK constraints are inherited.
- **`ATTACH PARTITION`** support.
- **CTE-aware join inference**: `JOIN` nodes inside `WITH` clauses infer
  relationships.
- **Dynamic SQL, `IF` and `CASE` extraction** from function and procedure
  bodies (`statements`, `conditions`, `writes`, `reads`).
- **Provenance** on tables, constraints, foreign keys, functions, triggers and
  raw objects.
- **Raw object storage** for views, materialized views, rules, policies,
  sequences, extensions, composite types and non-unique indexes.
- `WHEN` and `UPDATE OF` captured on triggers.

### Measured

- 97 tests passing, including CTE inference, dynamic SQL, `LIKE`/`INHERITS`
  and `ATTACH PARTITION`.

## [0.4.0] — 2026-11-10

### Added

- **Offline schema dump loader.** `pgproof load-dump db/schema.sql` builds the
  catalog from a `.sql` file: tables, columns, constraints, enums, domains,
  partitions, functions, triggers, views and simple unique indexes.
- **`--schema-file`** on `plan`, `audit`, `generate`, `verify` and `run`. The
  source DSN becomes optional for generation; the target DSN is still required.
- **Inferred foreign keys** from `*_id` naming and from `JOIN` conditions in
  views. Inferred keys carry `ForeignKey.inferred = True`.
- `sqlglot` as a dependency for PostgreSQL DDL parsing.

### Measured

- Dump-only billing: 10 tables, 500 rows, 93 invariants PASS.
- Dump-only partitioned schema: 300 rows, 29 invariants PASS.
- 91 tests passing.

### Known limits

- PL/pgSQL bodies are stored but not parsed; triggers are inventoried, not
  replayed.
- `CREATE RULE`, event triggers, policies, `LIKE`, `INHERITS` and
  `ATTACH PARTITION` are not applied.
- Inferred foreign keys are heuristic.

## [0.3.2] — 2026-11-10

### Added

- **Schema filters.** `--schema`, `--include` and `--exclude` restrict
  introspection on every command that reads a catalog.
- **Multi-column partition keys.** Range and list bounds are parsed as tuples
  and generation stays inside the lexicographic bounds.
- **Sub-partitioning.** The partition tree is introspected recursively; the
  generator flattens it into leaf paths and satisfies every level's bounds.
- **Trigger function deduplication.** Function bodies are stored once per OID;
  triggers reference them by `function_oid`.
- `pgproof audit` and `pgproof profile` honor the catalog filters.

### Measured

- 500 triggers sharing one function produce one function body.
- A sub-partitioned table routes rows by month and level; a multi-column range
  splits tenants 1–99 and 100–199.
- 82 tests passing.

## [0.3.1] — 2026-11-10

### Added

- **`TABLESAMPLE` sampling.** Tables above 50k rows are sampled with
  `TABLESAMPLE SYSTEM` and `REPEATABLE(seed)`; `BERNOULLI` is available with
  `--sample-method bernoulli`. Below the threshold, and on partitioned parents,
  `LIMIT` is used.
- **Date, timestamp and time histograms.** Buckets are computed on a numeric
  axis and converted back during generation (`bucket_kind` in the profile).
- `--sample-method` and `--sample-seed` on `pgproof profile`.
- `sample_method`, `sample_pct` and `bucket_kind` in the profile JSON.

### Measured

- 60k-row table, 500-row `SYSTEM` sample (0.83% of blocks), 19 date buckets;
  generation with that profile passed.

## [0.3.0] — 2026-11-04

### Added

- **Partitioned table routing.** Introspection reads `pg_partitioned_table` and
  `pg_get_expr(relpartbound)`, excludes child partitions from the table list,
  and attaches them as metadata to the parent.
- Range partitions sample inside `[lower, upper)`.
- List partitions sample from the declared values.
- Hash partitions let PostgreSQL route the row.
- A partition key that is the whole primary key is generated with a counter to
  guarantee uniqueness.
- Fixture `examples/partitioned-events` with 12 range partitions, 2 list
  partitions and 4 hash partitions.

### Measured

- 60 rows routed across 12 range partitions and 4 hash partitions in the
  fixture.
- Introspection reports 3 parents, not 19 relations.
- 68 tests passing.

### Known limits

- Sub-partitioning and multi-column partition keys are not supported yet; the
  planner emits a warning.

## [0.2.1] — 2026-11-04

### Fixed

- **Iterative Tarjan SCC.** A reverse-ordered foreign-key chain of 1,200 tables
  no longer hits `RecursionError`. A 5,000-table chain is covered by a test.
- **Batched `TRUNCATE`.** Tables are truncated 100 at a time, so a schema with
  thousands of tables does not exhaust `max_locks_per_transaction`.

### Measured

- 1,200 tables × 1,000 rows (1.2M rows, 9,599 invariants): 28 s, 173 MB.
- 5,000-table reverse chain: plans successfully.

## [0.2.0] — 2026-11-02

### Added

- **JUnit XML renderer** (`report.xml`) for GitHub Actions, GitLab, Jenkins and
  Buildkite.
- **`--fail-on never|error|fail`** policy, including shadow mode.
- **Repair loop**: the spec accepts `repair:` and the pipeline runs
  generate → repair → verify with a fixed-point limit (`--max-passes`).
- **`pgproof profile`**: per-column null fraction, distinct count, top values
  and 20-bucket histograms.
- **Distribution-aware generation** via `--profile` (categorical, numeric and
  boolean), with constraint safety.
- **`pgproof audit`**: Markdown schema readiness audit for pilots.
- **GitHub Action** (`action.yml`) with annotations and job summary.
- Report `format_version: 2` with a `repairs` section.
- Dataset digest recomputed from the target after repairs.

### Fixed

- Composite unique keys are enforced deterministically.
- Scope-aware foreign keys keep `tenant_id` consistent across tables.
- Strict ranges (`> 0`) no longer accept the boundary value.
- Numeric values sampled from a profile are quantized to the column scale.

## [0.1.1] — 2026-11-02

### Added

- Structured `LoadError`: table, constraint, SQLSTATE, row, values, detail and
  context.
- Deterministic replay to recover the offending row from a `COPY` line number.
- Four fixtures: SaaS billing, e-commerce, multi-tenant CRM, analytics.
- Clean architecture: ports, use cases and adapters.
- Architecture guards in the test suite.
- Ruff clean.

## [0.1.0] — 2026-11-02

### Added

- PostgreSQL 14–17 introspection from `pg_catalog`.
- Invariants from NOT NULL, UNIQUE, primary keys, foreign keys, CHECK and enums.
- Deterministic generation with pre-allocated keys and `COPY`.
- Local rules: ranges, `IN`, arithmetic derivations and column orderings.
- YAML spec for application rules.
- Independent verifier with violation samples.
- JSON and HTML reports with limitations.
- CLI: `introspect`, `plan`, `generate`, `verify`, `run`.
