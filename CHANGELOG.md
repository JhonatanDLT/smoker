# Changelog

All notable changes to pgproof. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses semantic
versioning.

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
