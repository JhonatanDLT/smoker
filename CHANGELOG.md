# Changelog

All notable changes to pgproof. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses semantic
versioning.

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
