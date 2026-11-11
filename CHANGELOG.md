# Changelog

All notable changes to smoker. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses semantic
versioning.

## [Unreleased]

### Added

- **Realistic schema pack** in `examples/realistic/`: six domains for
  PostgreSQL and SQL Server (billing, e-commerce, fintech, logistics,
  multi-tenant CRM and healthcare), plus `scripts/run_realistic.sh`.
  All twelve combinations generate and verify at 500 rows.
- One-to-one relationships: a unique constraint made only of foreign-key
  columns now assigns each parent row once instead of retrying and colliding.
- Composite keys that contain a foreign-key column now reuse the parent's
  identifier, so the pre-allocated key is valid for both the key and the FK.

### Fixed

- Partial unique indexes whose key is an immutable FK now move the row out of
  the predicate instead of failing with a duplicate.
- PostgreSQL enum predicates with a schema-qualified cast
  (`status = 'active'::billing.subscription_status`) are evaluated correctly.
- SQL Server `IN` checks normalized to `col = 'a' OR col = 'b'` are parsed as
  allowed values, so generated data respects them.
- Bracketed arithmetic checks no longer produce a spurious copy rule that
  overwrote the computed column.
- SQL Server temporal introspection only marks system-versioned tables; plain
  tables no longer receive a fake history table.
- SQL Server digests and profiling support `datetimeoffset` columns on pyodbc
  versions that reject ODBC type `-155`.

### Measured

- 175 tests; all pass with PostgreSQL 16 and SQL Server 2022 Developer.

## [0.10.0] — 2026-11-11

### Changed

- **Renamed the project from `pgproof` to `smoker`.**
  - Python package, CLI entry point, GitHub Action, environment variables
    (`SMOKER_*`), report labels, documentation and examples now use the new
    name.
  - The rule engine, dialects, adapters and report formats are unchanged.
  - Existing databases and test schemas were renamed for consistency.

### Measured

- 170 tests; all pass with PostgreSQL 16 and SQL Server 2022 Developer.

## [0.9.9] — 2026-11-11

### Added

- **PostgreSQL partial unique indexes.** Live introspection and the dump
  loader keep `UniqueConstraint.predicate`, so generation and verification
  scope uniqueness exactly like SQL Server filtered indexes.
- **`Catalog.warnings`.** Unsupported objects (for example expression-based
  unique indexes) are reported instead of silently dropped.
- Unique-key adjustment now advances numerically on every attempt. A unique
  key whose predicate references the key column itself no longer blocks its
  own adjustment.

### Measured

- 170 tests; all pass with PostgreSQL 16 and SQL Server 2022 Developer.

## [0.9.8] — 2026-11-11

### Added

- **Parallel verification** with `--jobs N` on `verify` and `run`.
  - One connection per worker; checks are split into contiguous chunks.
  - Report order, ids and verdict are identical to a serial run.
  - Worker connections are closed before the report is returned; if they
    cannot be opened, verification falls back to the primary connection.
- Thread-pool and worker-lifecycle unit tests; SQL Server parallel verification
  integration test.

### Measured

- 167 tests; all pass with PostgreSQL 16 and SQL Server 2022 Developer.

## [0.9.7] — 2026-11-11

### Added

- **Temporal invariants** for SQL Server system-versioned tables.
  - History periods must be ordered, never inverted and never overlap
    (window function over the history table).
  - Current rows must not overlap their own history.
  - New `temporal` invariant kind, included in JSON/HTML/JUnit reports.
  - Zero-length history periods are allowed: SQL Server records them when a
    row changes more than once in one transaction.
- Integration test that corrupts history with versioning disabled and proves
  both temporal invariants fail.

### Measured

- 164 tests; all pass with PostgreSQL 16 and SQL Server 2022 Developer.

## [0.9.6] — 2026-11-11

### Added

- **Temporal history generation** for SQL Server system-versioned tables.
  - Introspection reads `temporal_type`, `history_table_id` and `sys.periods`.
  - `Table.temporal` carries history table and period columns.
  - `--temporal-history` on `generate`/`run` performs a deterministic,
    constraint-aware `UPDATE` on each temporal table so SQL Server writes
    history rows.
  - Temporal tables use `DELETE` instead of `TRUNCATE` during generation.
- **BULK INSERT writer** for on-prem SQL Server (`--method bulk`).
  - TSV staging with `BULK INSERT`, `TABLOCK`, `CHECK_CONSTRAINTS`,
    `KEEPIDENTITY`, `FIRE_TRIGGERS`.
  - `--bulk-dir` (server path) and `--bulk-local-dir` (client path) support
    shared volumes and Docker.
  - Azure SQL rejects it (`bulk_insert_from_file=False`).
- SQL Server integration tests for temporal history and BULK INSERT (the latter
  gated by `SMOKER_MSSQL_BULK_LOCAL`/`SMOKER_MSSQL_BULK_SERVER`).

### Measured

- 164 tests; all pass with PostgreSQL 16 and SQL Server 2022 Developer.

## [0.9.5] — 2026-11-11

### Added

- **Multi-database SQL Server catalogs.** `--databases app,audit` (or `*`)
  builds one catalog with `database.schema.table` keys. Tables, foreign keys,
  functions, views and inferred relationships carry the database qualifier.
- **Three-part SQL rendering** for SQL Server invariants, writer, query runner,
  profiler, SQL export, sequence reseed and `IDENTITY_INSERT`.
- **Cross-database inference** from `*_id` naming; the generator produces
  coherent values across databases on on-prem SQL Server.
- **Azure SQL dialect** (`--dialect azuresql`):
  - single database only,
  - no `USE` or cross-database references,
  - no local-file `BULK INSERT`,
  - transient connection retries for serverless/failover SQLSTATES.
- Azure SQL documentation (`docs/azure-sql.md`).
- T-SQL dump loader handles `USE [db]`, three-part names and cross-database view
  dependencies.

### Changed

- `Table`, `ForeignKey`, `Function` and `SqlObject` carry an optional
  `database`; single-database catalogs keep their existing `schema.table` keys.
- PostgreSQL multi-database requests fail explicitly, because one connection is
  one database.

### Measured

- 158 tests; all pass with PostgreSQL 16 and SQL Server 2022 Developer.

## [0.9.4] — 2026-11-11

### Added

- **Filtered unique indexes** are real constraints now.
  - `UniqueConstraint.predicate` captures the SQL Server `WHERE` clause.
  - Introspection and T-SQL dump loading preserve the filter.
  - The generator enforces uniqueness only for rows that satisfy the predicate;
    inactive rows may repeat the key.
  - The model is dialect-neutral; PostgreSQL partial-index extraction can reuse
    it later.
  - `unique_sql` includes the predicate, so verification checks the filtered
    scope rather than the whole table.
- Bracket and cast normalization for predicates (`[active]=(1)`, `::text`).
- Numeric unique-key collision adjustment, not only text suffixes.
- Filtered unique coverage in the SQL Server integration suite, including
  duplicate inactive rows.

### Fixed

- SQL Server check constraints with bracketed identifiers and parenthesized
  bounds are now parsed into local generation rules.

### Measured

- 151 tests; all pass with PostgreSQL 16 and SQL Server 2022 Developer.

## [0.9.3] — 2026-11-11

### Added

- **CI matrix for PostgreSQL and SQL Server.** `.github/workflows/ci.yml` runs
  PostgreSQL 16 and SQL Server 2022 Developer service containers, installs
  ODBC Driver 18, runs unit tests, prepares the PostgreSQL fixture and runs the
  integration suite with both DSNs.
- Documentation for the SQL Server CI service container.

### Measured

- 148 tests, including the SQL Server integration suite.

## [0.9.2] — 2026-11-11

### Added

- **T-SQL dump loader** (`adapters/sqlserver/dump.py`): `GO` batches,
  bracketed identifiers, `IDENTITY`, computed columns, `ROWVERSION`, defaults,
  checks, FKs, unique indexes, views, stored procedures, functions, triggers,
  sequences and user-defined types. `load-dump --dialect sqlserver` works.
- **Stored procedures** are exposed with `kind="procedure"`, parameters, body,
  `writes`/`reads`, `conditions` and a dedicated `procedure` creation-order
  phase. The audit reports function/procedure counts and a visibility risk.
- **SQL Server profiler**: `TOP`/`TABLESAMPLE` sampling, null fractions, distinct
  counts, top values and date/numeric buckets.
- **SQL Server range partitions** mapped from partition functions/schemes into
  the canonical `Partition` tree; generation samples inside the boundaries.
- **T-SQL segmentation extraction**: `IF ... THROW/RAISERROR` guards from
  procedures and triggers become report-only candidates with provenance.

### Fixed

- SQL Server `sys.objects.type` comparison for procedures.
- Compound/`EXISTS` trigger conditions are not mis-negated into invalid rules.
- SQL Server integration now covers dump → server → generate → verify.

### Measured

- 148 tests; all pass with PostgreSQL 16 and SQL Server 2022 Developer.

## [0.9.1] — 2026-11-11

### Verified against a real SQL Server

The adapter was exercised against **SQL Server 2022 Developer** in a Docker
container (`mcr.microsoft.com/mssql/server:2022-latest`) on an x86_64 Colima VM.

### Fixed

- `SqlServerConnection.close()` is idempotent; closing a writer and then the
  connection no longer raises.
- `sys.sequences` `sql_variant` columns are cast to `nvarchar` before pyodbc
  reads them; introspection no longer fails on a sequence.
- `DBCC CHECKIDENT` is emitted as one batch with its `DECLARE`/`SELECT`; the
  variable is no longer out of scope.
- SQL scripts begin with `SET ANSI_NULLS ON` and `SET QUOTED_IDENTIFIER ON`, so
  computed columns and filtered indexes accept `DELETE`/`INSERT`.
- `DataKind.BYTEA` now generates `bytes` instead of a PostgreSQL `\x...`
  string; SQL Server `varbinary` and PostgreSQL `bytea` both receive the right
  value.
- `generate`/`run` with `--catalog` no longer require a source DSN.

### Added

- Integration coverage for:
  - source introspection and verification;
  - identity + uniqueidentifier + datetime2 + varbinary + computed + rowversion;
  - SQL script export applied to SQL Server and then verified.

### Measured

- 141 tests; all pass with `SMOKER_TEST_DSN` and `SMOKER_MSSQL_DSN` set.

## [0.9.0] — 2026-11-11

### Added

- **SQL Server adapter (experimental, source-first).**
  - `adapters/sqlserver/dialect.py`: T-SQL quoting (`[x]`), `N'...'` literals,
    type mapping, `COUNT_BIG`, `TOP`, `WHERE NOT (...)`, script statements and
    `IDENTITY_INSERT`/`DBCC CHECKIDENT`.
  - `adapters/sqlserver/introspect.py`: `sys.tables`, `sys.columns`,
    `sys.indexes`, `sys.foreign_keys`, `sys.check_constraints`, triggers,
    functions, views, sequences and partition schemes.
  - `adapters/sqlserver/query_runner.py`: count, sample, identity, table counts,
    untrusted constraints and deterministic dataset digest.
  - `adapters/sqlserver/writer.py`: `pyodbc` `fast_executemany`, identity insert,
    FK-aware delete order and reseed.
  - `adapters/sqlserver/sql_executor.py` for repair statements.
  - Registered as `--dialect sqlserver`; the optional dependency is
    `smoker[sqlserver]`.
- Integration tests gated by `SMOKER_MSSQL_DSN` for source verification and
  generation.
- T-SQL dialect contract tests (rendering, types, scripts) that run without a
  SQL Server instance.

### Known gaps in 0.9.0

- T-SQL dump loading is a clear `NotImplementedError`; use a live connection.
- SQL Server profiling is a clear `NotImplementedError`.
- SQL Server integration has not run in this environment; it is exercised when
  `SMOKER_MSSQL_DSN` is set.
- SQL Server partitioning is inventoried as raw objects, not mapped to the
  partition tree yet.

### Measured

- 140 tests collected; 138 pass locally and 2 SQL Server integration tests skip
  without a DSN.

## [0.8.0] — 2026-11-11

### Added

- **Dialect layer** (`core/dialect.py`): `SqlDialect` and `Capabilities`.
  The core now renders every invariant through an injected dialect instead of
  hardcoding PostgreSQL SQL.
- **`PostgresDialect`** (`adapters/postgres/dialect.py`) implements quoting,
  literals, type classification, constraint SQL, query wrapping and SQL-script
  statements.
- **Dialect registry** (`adapters/registry.py`) with `get_adapter`,
  `get_dialect`, `get_ddl_loader` and `register_adapter`.
- **`DatabaseAdapter` aggregate** (`adapters/base.py`) bundling dialect, DDL
  loader, connect, introspection, writer, query runner, executor and profiler.
  The CLI no longer imports a backend module directly.
- **`DdlLoader` port** so `load-dump` and `--schema-file` can select a parser
  per dialect.
- **Dialect-aware SQL export**: `SqlFileWriter` now takes a `SqlDialect` and
  delegates transactions, truncate, sequence reseed and identity handling.
- **`--dialect`** on `load-dump`, `plan`, `order`, `audit`, `generate`,
  `verify` and `run`. Only `postgres` is implemented; other names fail with a
  clear message.
- **`dialect` field** in the JSON report and the HTML summary.
- Architecture guard: `core/invariants.py` must not contain PostgreSQL SQL.

### Changed

- `invariants_from_catalog(catalog, dialect)` is now dialect-driven.
- `VerifyDataset` and `AuditSchema` receive a `SqlDialect`.
- `adapters/base.py` is a compatibility shim; the contracts live in
  `core/dialect.py`.
- Report limitations no longer claim that exclusion constraints are only
  reported.

### Measured

- 132 tests passing.

## [0.7.1] — 2026-11-10

### Added

- **Exclusion constraints now bound generation.** `core/exclusions.py` detects
  conflicts against accepted rows and adjusts a non-key column deterministically:
  equality columns advance, range columns shift, and key/FK/partition columns are
  never touched.
- Range types (`int4range`, `int8range`, `numrange`, `daterange`, `tsrange`,
  `tstzrange`) are generated as `[lower, upper)` literals.
- Live introspection reads `contype = 'x'` into `ExclusionConstraint` instead of
  an opaque check.
- `EXCLUDE` predicates are evaluated for simple boolean/comparison expressions;
  unknown predicates are enforced globally.
- Integration test loads `EXCLUDE USING btree`, `EXCLUDE USING gist` and a
  predicate-filtered exclusion into PostgreSQL and verifies the report.

### Changed

- Unsupported exclusion operators fail generation loudly instead of emitting
  rows that the database would reject.

### Measured

- 123 tests passing.

## [0.7.0] — 2026-11-10

### Changed

- **Scope correction:** only declared table constraints define the generation
  space. Trigger/function guards, `NEW.col :=` assignments and view filters are
  segmentation rules; they are reported with provenance but are never added to
  `table.checks` and never change the generation space.
- `RuleCandidate.kind` is now `guard`, `derived` or `filter`, with
  `category="segmentation"` and `applied=False` by design.
- The HTML report labels rules as segmentation and no longer suggests they are
  enforced constraints.

### Added

- **SQL INSERT export** without a database:
  `smoker generate --schema-file db/schema.sql --rows 1000 --sql data.sql`.
  The file contains `BEGIN`, optional batched `TRUNCATE`, batched `INSERT`
  statements, `setval` calls for identity columns and `COMMIT`.
- `sql_literal` for safe SQL value rendering.
- Integration test: generate a SQL file from a dump, apply it with `psql -f`,
  and verify row counts and zero orphan foreign keys.

### Measured

- 113 tests passing.

## [0.6.0] — 2026-11-10

### Added

- **Dependency-safe creation order** (`smoker order`):
  extensions → enums → types → domains → sequences → tables (FK order) →
  functions → indexes → triggers → views → materialized views → rules →
  policies → comments/grants.
- `depends_on` on every step, view-to-view ordering and foreign-key cycle
  reporting.
- `creation_order` in `load-dump` and `plan` JSON output.
- **Exclusion constraints** parsed from `CREATE TABLE` and `ALTER TABLE`,
  including `USING`, elements and `WHERE` predicates, and verified as declared
  constraints with a `ctid` self-join.
- **View filters** extracted as `kind="filter"`, `scope="aggregate"`
  candidates. They are report-only and never add checks to the base table.
- `RuleCandidate.scope` (`row` / `aggregate`) and a Scope column in the HTML
  report.
- Enum and domain objects stored in `Catalog.objects` so the creation order can
  list them.

### Fixed

- `CREATE EXTENSION IF NOT EXISTS` no longer captures `IF` as the extension name.
- Exclusion `WHERE` predicates are qualified with the `a` alias.

### Measured

- 110 tests passing.

## [0.5.0] — 2026-11-10

> **Superseded in 0.7.0:** extracted rules are segmentation candidates, not
> constraints. They are no longer added to `table.checks`.

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

- **Offline schema dump loader.** `smoker load-dump db/schema.sql` builds the
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
- `smoker audit` and `smoker profile` honor the catalog filters.

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
- `--sample-method` and `--sample-seed` on `smoker profile`.
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
- **`smoker profile`**: per-column null fraction, distinct count, top values
  and 20-bucket histograms.
- **Distribution-aware generation** via `--profile` (categorical, numeric and
  boolean), with constraint safety.
- **`smoker audit`**: Markdown schema readiness audit for pilots.
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
