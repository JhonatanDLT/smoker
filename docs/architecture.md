# Architecture

## Layers

```
┌──────────────────────────────────────────────┐
│ cli.py                    composition root   │  typer, psycopg, yaml
│  parses args, wires adapters, formats output │
└───────────────┬──────────────────────────────┘
                │ depends on
┌───────────────▼──────────────────────────────┐
│ application/              use cases          │  pure orchestration
│  GenerateDataset · RepairDataset ·           │
│  VerifyDataset · RunPipeline                 │
└───────────────┬──────────────────────────────┘
                │ depends on
┌───────────────▼──────────────────────────────┐
│ core/                     domain + ports     │  no drivers, no CLI, no I/O
│  ir · graph · invariants · planner ·         │
│  generator · verifier · report · ports ·     │
│  errors                                      │
└───────────────▲──────────────────────────────┘
                │ implements ports
┌───────────────┴──────────────────────────────┐
│ adapters/                 infrastructure     │
│  postgres/ (introspect, profile, writer,     │
│             query_runner, errors)            │
│  spec/ (YamlSpecLoader)                      │
│  report/ (JsonRenderer, HtmlRenderer,        │
│           FileReportWriter)                  │
└──────────────────────────────────────────────┘
```

The dependency rule is enforced by `tests/test_architecture.py`:

- `core` may not import `psycopg`, `typer`, `yaml`, `adapters`, `application` or `cli`.
- `application` may not import adapters or infrastructure.
- `cli` is the only place that constructs concrete adapters.

## Ports and adapters

| Port | Responsibility | Adapter |
|---|---|---|
| `CatalogReader` | Read a schema into the domain model | `postgres.introspect` |
| `RowWriter` | Truncate, write rows, reset sequences, commit/rollback | `PostgresRowWriter` |
| `QueryRunner` | Count violations, sample rows, read identity and warnings | `PostgresQueryRunner` |
| `SqlExecutor` | Run repair statements and control their transaction | `PostgresSqlExecutor` |
| `SpecLoader` | Turn a file into application invariants | `YamlSpecLoader` |
| `ReportRenderer` | Render one format | `JsonRenderer`, `HtmlRenderer` |
| `ReportWriter` | Persist every rendered format | `FileReportWriter` |

Adding SQL Server means adding `adapters/mssql/` implementing the first three
ports. Adding JUnit XML means adding one `ReportRenderer`. Neither touches the
domain.

## SOLID mapping

**Single responsibility.** `ir` models the schema. `graph` orders tables.
`invariants` extracts rules. `generator` produces rows. `verifier` checks them.
`report` holds the result. Each use case orchestrates one workflow. Each adapter
talks to one system.

**Open/closed.** New dialect: new adapter. New report format: new renderer. New
spec format (JSON, TOML): new `SpecLoader`. No `if dialect == ...` branches in
the core.

**Liskov substitution.** Every adapter honours its port. Features a dialect does
not support are reported as `skipped`, never raised as a silent exception. The
`Capabilities` dataclass in `adapters/base.py` documents the differences.

**Interface segregation.** Six small ports instead of one large `Database`
interface. A renderer does not need a connection; a writer does not need a
query runner.

**Dependency inversion.** The core defines the protocols; the adapters depend on
them. The CLI injects concrete implementations. `core` has zero third-party
imports beyond the standard library.

## Data flow

```
1. introspect(conn)              → Catalog
2. build_plan(catalog, rows)     → Plan (order, cycles, budgets)
3. Generator(catalog, plan)      → row streams (profile-aware)
4. RowWriter.write(table, …)     → COPY into the target
5. RepairDataset.execute(…)      → deterministic repair statements
6. invariants_from_catalog()     → [Invariant]
   + SpecLoader.load()           → application rules
7. verify_all(QueryRunner, …)    → Verification
8. build_report(…)               → Report (format_version 2)
9. FileReportWriter.write(…)     → report.json + report.html + report.xml
```

## Introspection

The catalog is the source of truth; we do not parse dumps when a live connection
exists.

| Need | Source |
|---|---|
| Tables and partitions | `pg_class` (`relkind` in `r`, `p`) |
| Columns, defaults | `pg_attribute`, `pg_attrdef`, `pg_get_expr` |
| Types and enums | `pg_type`, `pg_enum` |
| Constraints | `pg_constraint`, `pg_get_constraintdef` |
| Triggers and functions | `pg_trigger`, `pg_proc`, `pg_get_functiondef` |
| Sequences | `pg_get_serial_sequence`, `pg_sequence` |

`pg_get_constraintdef` normalizes predicates, for example:
```
CHECK (amount = (quantity::numeric * unit_price))
```

The extractor strips `CHECK (...)` and the rule parser tolerates casts,
parentheses and `NOT VALID`.

### Filters and function deduplication

`--schema`, `--include` and `--exclude` are applied after introspection by
`core/filters.py`. They use shell globs against `schema.table` and the bare
table name, and they also work with a saved catalog.

Trigger function bodies are stored once per OID in `Catalog.functions`. Each
`Trigger` keeps a `function_oid`, so 500 triggers that share one function
produce one body, not 500.

## Generation

### Pre-allocated keys

Every table's primary key and unique keys are materialized before any row is
written. A child can therefore reference a parent that has not been generated
yet, which makes foreign-key cycles work without recursion.

### Topological order

`graph.py` builds child → parent edges and condenses strongly connected
components with an **iterative** Tarjan pass. Parents come first; cycles are
collapsed and reported. The iterative implementation exists because a
reverse-ordered chain of 1,200 tables is enough to hit Python's recursion limit;
a 5,000-table chain is covered by a test.

The target is truncated in batches of 100 tables, so a schema with thousands of
tables does not exhaust `max_locks_per_transaction`.

### Row construction

1. Assign primary-key values from the pool.
2. Assign foreign keys from parent pools.
3. Generate the remaining columns from SQL type and semantic name.
4. Apply derived rules (`amount = quantity * unit_price`).
5. Apply ordering rules (`ends_at > starts_at`).
6. Enforce composite unique keys.
7. Validate against local rules; retry with fresh values.
8. Index the row for scope-aware foreign keys.

### Scope-aware foreign keys

In a multi-tenant schema, `contacts.company_id` must point at a company in the
**same tenant** as the contact. The generator detects a scope column when it is:

- present in both tables,
- a foreign key in both tables (for example `tenant_id` → `tenants`),
- not the foreign key being assigned.

It then picks a parent row with the same scope, or adopts the parent's scope if
the current one has no rows. Common columns such as `id` or `name` are
deliberately ignored.

### Partitioned tables

Introspection excludes child partitions from the table list and stores them as a
tree on the parent. `Partition` carries the bounds:

- **range**: `lower` / `upper`, as lists for multi-column keys
- **list**: `values`, a list of tuples
- **hash**: `modulus` / `remainder`
- **sub-partitioning**: `children`

The generator flattens the tree into leaf paths, picks a leaf weighted by
estimated rows, and samples each level's key inside its bounds. A tuple is
checked against every range and list constraint on the path before it is
accepted; otherwise it is resampled.

When the partition key is the whole primary key and it has a single column, a
per-table counter guarantees uniqueness. Multi-column and sub-partitioned
key-only primary keys are warned about in the plan.

### Determinism

Each table has its own `random.Random` seeded with `f"{seed}:{table}"`. Retries
consume the same stream in the same order, so two runs with the same seed
produce the same rows and the same SHA-256 digest.

### Distributions

`pgproof profile` samples the source and produces `core.profile.Profile`:
null fraction, distinct count, top values and 20-bucket histograms. On tables
above 50k rows it uses `TABLESAMPLE SYSTEM` with `REPEATABLE(seed)`; below that,
`LIMIT`. Partitioned parents use `LIMIT` because PostgreSQL does not support
`TABLESAMPLE` on them. Dates, timestamps and times are converted to a numeric
axis (`toordinal`, epoch seconds, seconds since midnight), bucketed, and
converted back by `coerce_value` during generation.

The generator asks `build_distribution` for a column and samples from it before
falling back to semantic rules.

```
profile → CategoricalDistribution | NumericDistribution | BooleanDistribution
        → sampled value → local rules → unique suffix → row
```

Constraint correctness always wins: a sampled value that violates a range or a
check is resampled. Distributions are skipped for samples below 50 rows, for
unique identifiers, and for columns where the top-k does not describe the
column.

### Repairs

A spec invariant may declare `repair:`. `RepairDataset` runs those statements
in a transaction, up to `max_passes` times, and stops at a fixed point. It
records before/after violations and rows affected. The final dataset digest is
computed from the target after repairs, so the certificate describes the data
the team actually has.

Repairs are explicit on purpose. Inverting arbitrary SQL is a research problem;
a declared `UPDATE` is auditable and safe.

## Verification

| Level | What it checks |
|---|---|
| L0 | Structural: constraints exist and are validated |
| L1 | Every extracted invariant, as a violation query |
| L2 | Row counts and per-table totals |
| L3 | Application rules from the spec |
| L4 | Reproducibility: dataset hash |

`WHERE (predicate) IS FALSE` is used for checks because SQL constraints pass on
`NULL`, and `NOT (NULL)` would silently skip violations.

## Error diagnostics

Database errors are translated into `LoadError` with structured fields:

```
load failed in debug.bad
  reason:     new row for relation "bad" violates check constraint "name_fixed"
  constraint: name_fixed
  sqlstate:   23514
  row:        1
  values:     id=1, name='Jorge Flores'
  detail:     Failing row contains (1, Jorge Flores).
  context:    COPY bad, line 1: "1	Jorge Flores"
```

For `COPY`, PostgreSQL reports a line number; the application layer replays the
deterministic generator up to that line to recover the offending row values.
For `INSERT`, the row is captured directly.

## Report

The JSON is canonical (`format_version: 2`). HTML and JUnit XML are renderings.
They include the verdict, per-invariant status and samples, skipped items,
repairs (rule, pass, before/after, rows affected), limitations, the reproducer
command, and both schema and dataset hashes.

A certificate that hides its gaps is worse than no certificate.

## Dump loading

`adapters/postgres/dump.py` builds the same `Catalog` from a `.sql` file:

- `split_statements` handles dollar quotes, `COPY ... FROM stdin` blocks,
  psql meta-commands and comments.
- `sqlglot` parses `CREATE TABLE`, `ALTER TABLE`, `CREATE TYPE`, `CREATE INDEX`
  and `CREATE VIEW`.
- Regular expressions handle `CREATE FUNCTION`, `CREATE PROCEDURE`,
  `CREATE TRIGGER` and `CREATE DOMAIN`, whose procedural bodies are not part of
  the DDL grammar.
- A post-processing pass builds the partition tree and infers relationships.

Relationships come from declared foreign keys. Missing ones are inferred from
`*_id` naming and from `JOIN` conditions in views; inferred keys carry
`ForeignKey.inferred = True`.

Functions are stored once in `Catalog.functions`; triggers reference them by
name. In a dump there are no OIDs, so the loader assigns synthetic ones.

`--schema-file` makes the source DSN optional for `generate` and `run`, and
supplies the catalog for `verify`, `plan` and `audit`.

## Security

- Credentials come from the environment or `--dsn`, never from the report.
- Samples are kept in memory; the profiler stores aggregates.
- No telemetry. The CLI works fully offline.
- Generated data is synthetic; no production value is copied.

## Capacity

Measured on a local PostgreSQL 16 (a 4-vCPU VPS will be slower on CPU-bound
steps, but memory is not the constraint):

| Schema | Rows | Invariants | Time | Peak RSS |
|---|---:|---:|---:|---:|
| 300 tables × 1,000 | 300k | 2,399 | 6.9 s | 79 MB |
| 1,200 tables × 1,000 | 1.2M | 9,599 | 28 s | 173 MB |

`COPY` handles 50–150k rows/s on simple tables; Python generation is the
bottleneck. Memory is bounded by key pools, not by row data, which is streamed.
Reports for 1.2M rows and 9,599 invariants weigh 3.7 MB (JSON) and 1.6 MB
(HTML).
