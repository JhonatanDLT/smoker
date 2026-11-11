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
- `LIKE ... INCLUDING ...` copies columns, defaults and constraints from the
  source table. `INHERITS` copies columns and CHECK constraints, but not
  primary keys, unique constraints or foreign keys.
- `ALTER TABLE ... ATTACH PARTITION` joins a standalone table to a partition
  tree.
- A post-processing pass builds the partition tree, infers relationships from
  `*_id` naming and from `JOIN` nodes (including CTEs), and analyses function
  bodies for dynamic SQL, `IF`/`CASE` conditions and write/read targets.

Relationships come from declared foreign keys. Missing ones are inferred and
carry `ForeignKey.inferred = True`.

Every table, constraint, foreign key, function, trigger and raw object carries
a `Provenance` (`dump`, `inferred`, `function`, `view`, `inherited`). Raw
objects (views, materialized views, rules, policies, sequences, extensions,
composite types and non-unique indexes) are stored in `Catalog.objects`.

Functions are stored once in `Catalog.functions`; triggers reference them by
name. In a dump there are no OIDs, so the loader assigns synthetic ones.

`--schema-file` makes the source DSN optional for `generate` and `run`, and
supplies the catalog for `verify`, `plan` and `audit`.

## Constraint space vs segmentation

Only the declared table constraints define the generation space:

- column types, enum values and domains
- `NOT NULL`
- primary keys and `UNIQUE`
- foreign keys
- `CHECK`
- `EXCLUDE`

Everything else is **segmentation**: trigger and function guards, assignments,
view filters and application rules. Segmentation describes how to choose rows
inside the space bounded by the table constraints; it never expands or shrinks
that space. `RuleCandidate.applied` is `False` by design for every segmentation
rule, and `Catalog.rules` is never merged into `table.checks`.

## Segmentation extraction

Trigger and function bodies are scanned after the catalog is built:

- `_if_blocks` walks `IF ... THEN ... END IF`, including nested blocks, without
  being confused by `END IF` or `ELSIF`.
- `_negate_condition` turns `IF <condition> THEN RAISE EXCEPTION` into a guard
  candidate `NOT <condition>` and normalizes simple forms (`<`, `<=`, `=`,
  `IN`, `IS NULL`, `BETWEEN`, `LIKE`).
- `NEW.col := expression` becomes a derived candidate `col = expression`.
- `_identifiers` separates column references from keywords, functions and type
  names.
- `_derived_confidence` grades the candidate: `0.9`–`0.95` for deterministic
  column expressions, `0.6` for unknown functions, `0.4` for `now()`/`random()`,
  `0.3` for self-referential assignments and `0.2` for dynamic SQL.

Rules travel in `Catalog.rules` as `RuleCandidate` objects with `kind`
(`guard`, `derived` or `filter`), `category="segmentation"`, `scope`
(`row` or `aggregate`), `table`, `expression`, `columns`, `confidence`,
`applied=False`, `note` and `provenance` (`trigger`, `function` or `view`).
They are shown in the report for review and provenance, but they do not change
the generation space and do not fail a verification run.

## SQL export

`adapters/script.py` implements the same `RowWriter` port as
the connected writer:

```bash
pgproof generate --schema-file db/schema.sql --rows 1000 --sql data.sql
```

It emits `BEGIN`, optional batched `TRUNCATE`, batched `INSERT` statements and
`COMMIT`, plus `setval` calls for identity/serial columns. Transactions,
truncate, sequence reset and identity handling come from the active
`SqlDialect`; the writer only batches rows and hashes them. It uses the same
canonical row text as the COPY writer, so the dataset hash matches an
equivalent connected run. `psql -f data.sql` applies the file to a schema
created from the dump.

## Creation and insertion order

`core/creation.py` turns a catalog into a dependency-safe list of steps. It does
not depend on the order of statements in the dump.

1. extensions
2. enum types
3. composite and range types
4. domains
5. sequences
6. tables in foreign-key order (`core/graph.py`, iterative Tarjan SCC)
7. functions and procedures
8. indexes
9. triggers
10. views and materialized views, ordered by view-to-view dependencies
11. rules
12. policies
13. comments and grants

`CreationStep.depends_on` holds the parent table keys, source tables, functions
or views a step needs. Foreign-key cycles are condensed and flagged with
`cycle = True`; view cycles are broken in name order and reported as warnings.
`pgproof order` writes the list as text or JSON, and `load-dump` and `plan`
embed the same structure.

## Exclusion constraints

`EXCLUDE` constraints are declared constraints, not code-derived candidates.
The dump loader reads `USING`, every `(column WITH operator)` element and the
optional `WHERE` predicate from both `CREATE TABLE` and `ALTER TABLE`. The
verifier turns them into a self-join over `ctid`.

The generator shapes rows to avoid overlaps. `core/exclusions.py` keeps the
projection of accepted rows for each table, evaluates the predicate, and checks
every element pair. When a conflict appears it adjusts one non-key column
deterministically:

- an equality element on a scalar column is advanced (`room = room + 1`, next
  text suffix, next enum label, next day/second);
- a range element (`&&`) is shifted past the previous range;
- columns that are primary keys, single-column unique keys, foreign keys,
  identity columns or partition keys are never adjusted.

Supported elements are `=`, `<>`, `<`, `<=`, `>`, `>=`, `&&`, `@>` and `<@`.
Range types (`int4range`, `int8range`, `numrange`, `daterange`, `tsrange`,
`tstzrange`) are generated as `[lower, upper)` literals. If an exclusion uses an
operator outside this set, generation fails loudly instead of producing a
violation.

View filters are a different case. A `WHERE` inside a view becomes a
`RuleCandidate` with `kind="filter"` and `scope="aggregate"`. It describes the
rows the view exposes, not a constraint on the base table, so it is never
applied as a check and never changes other candidates.

## Dialects and adapters

The engine is dialect-agnostic. SQL rendering lives behind a port:

```
core/dialect.py        SqlDialect, Capabilities
adapters/base.py       DatabaseAdapter (dialect + connection-bound factories)
adapters/postgres/     dialect.py, introspect.py, writer.py, ...
adapters/registry.py   get_adapter(), get_dialect(), get_ddl_loader()
```

`DatabaseAdapter` is the single composition unit:

```python
DatabaseAdapter(
    name="postgres",
    dialect=PostgresDialect(),
    ddl_loader=DumpLoader(),
    connect=psycopg.connect,
    introspect=introspect,
    writer=PostgresRowWriter,
    query_runner=PostgresQueryRunner,
    executor=PostgresSqlExecutor,
    profiler=profile_catalog,
)
```

The CLI asks the registry for one adapter and never imports a backend module.
That keeps the composition root small and makes a second backend a registry
entry plus a package.

`SqlDialect` owns:

- identifier quoting and literals
- type classification
- the violation SQL for NOT NULL, UNIQUE, FK, CHECK, enum and EXCLUDE
- count/sample wrapping (`LIMIT` vs `TOP`)
- SQL-script statements (BEGIN/COMMIT, TRUNCATE, sequence reset, identity
  insert)

`Capabilities` is data, not a branch on the dialect name: exclusion support,
native ranges/enums/arrays, partition model, identity/computed/rowversion,
`TRUNCATE ... CASCADE`, row identity (`ctid` vs primary key), `NOT VALID`,
filtered indexes, temporal tables, bulk method and sampling method.

`invariants_from_catalog(catalog, dialect)` receives the dialect; the core never
contains PostgreSQL SQL. `tests/test_architecture.py` fails if `ctid`, `::text`,
`IS FALSE`, `count(*)` or `ILIKE` reappear in `core/invariants.py`.

To add a dialect:

1. Implement `SqlDialect` in `adapters/<name>/dialect.py`.
2. Implement a `DdlLoader` for `.sql` dumps.
3. Implement the connection adapters (`introspect`, `writer`, `query_runner`,
   `executor`, `profiler`).
4. Build a `DatabaseAdapter` and register it with `register_adapter()`.
5. Pass the conformance suite: catalog, types, invariants, generation, errors,
   scripts and repair.

The CLI exposes `--dialect`; unknown names fail with the available list. Only
`postgres` is implemented today.

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
