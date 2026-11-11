# CLI reference

```
smoker [COMMAND] [OPTIONS]
```

## Conventions

### DSN resolution

Every command accepts `--dsn` or `--dsn-env`.

- `--dsn postgresql://user:pass@host/db` uses the value directly.
- `--dsn MY_ENV_VAR` reads that environment variable.
- If `--dsn` is omitted, the command reads its default environment variable.

| Command | Default variable |
|---|---|
| `introspect`, `audit`, `profile`, `plan`, `order` | `SMOKER_SOURCE_DSN` |
| `verify` | `SMOKER_TARGET_DSN` |
| `generate`, `run` | `SMOKER_SOURCE_DSN` and `SMOKER_TARGET_DSN` |

**Prefer environment variables over `--dsn`.** The command line is copied into
the report's `reproduce` field; a DSN passed as a flag would end up in the
artifact.

### Catalog filters

`--schema`, `--include` and `--exclude` restrict which tables are introspected.
They apply to `introspect`, `audit`, `profile`, `plan`, `order`, `generate`, `verify` and
`run`, and they also work with a saved `--catalog`.

| Option | Description |
|---|---|
| `--schema` | Comma-separated schema names |
| `--include` | Comma-separated globs matched against `schema.table` or the table name |
| `--exclude` | Same, but removes matches |

```bash
smoker run --schema billing --exclude "*.audit_*" --out proof
```

Patterns are shell globs. `billing.*` matches every table in `billing`;
`*_history` matches any table ending in `_history` in any schema.

### Dialects

`--dialect` selects the SQL renderer and the DDL loader:

- `postgres` (fully implemented)
- `sqlserver` (experimental, on-prem SQL Server)
- `azuresql` (managed service; single database, no `USE`/cross-database)

```bash
pip install "smoker[sqlserver]"
smoker verify --dialect sqlserver --dsn-env SMOKER_MSSQL_DSN
smoker verify --dialect azuresql --dsn-env SMOKER_AZURE_DSN
smoker generate --dialect sqlserver --databases app,audit \
  --source-env SMOKER_MSSQL_DSN --target-env SMOKER_MSSQL_DSN
```

`--databases app,audit` or `--databases '*'` builds a multi-database catalog for
on-prem SQL Server. Keys become `database.schema.table` and the writer/query
runner use three-part names. Azure SQL rejects multiple databases.
See [`azure-sql.md`](azure-sql.md).

T-SQL dump loading, profiling, stored procedures, range partitions and filtered
unique indexes are implemented. Temporal history and `BULK INSERT` are still
pending. The extension point is documented in
[`architecture.md`](architecture.md).

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Verification passed, or the policy allowed the result |
| `1` | Verification failed under the chosen `--fail-on` policy |
| `2` | Load, connection, spec or configuration error |

---

## `smoker version`

Prints the version.

---

## `smoker introspect`

Reads the catalog and writes JSON.

| Option | Default | Description |
|---|---|---|
| `--dsn` | — | DSN or env var name |
| `--dsn-env` | `SMOKER_SOURCE_DSN` | Env var with the DSN |
| `--out`, `-o` | `catalog.json` | Output file |

```bash
smoker introspect --dsn-env SMOKER_SOURCE_DSN -o catalog.json
```

The catalog is a reusable input for `plan` and `verify` via `--catalog`.

---

## `smoker load-dump`

Builds the catalog from a schema dump (`.sql`) without a database connection.

| Option | Default | Description |
|---|---|---|
| `FILE` | — | Path to the `.sql` dump |
| `--out`, `-o` | `catalog.json` | Output file |
| `--schema` | — | Comma-separated schemas |
| `--include` | — | Comma-separated globs |
| `--exclude` | — | Comma-separated globs |

```bash
smoker load-dump db/schema.sql -o catalog.json
```

The JSON also contains `creation_order`, the dependency-safe order described
under `smoker order`.

## `smoker order`

Prints the creation and insertion order for a catalog: extensions, enums,
types, domains, sequences, tables in foreign-key order, functions, indexes,
triggers, views, rules and policies.

| Option | Default | Description |
|---|---|---|
| `--schema-file` | — | Build the catalog from a `.sql` dump |
| `--dsn`, `--dsn-env` | `SMOKER_SOURCE_DSN` | Live source instead of a dump |
| `--format` | `text` | `text` or `json` |
| `--out`, `-o` | `order.txt` | Output file; use `-` for stdout |

```bash
smoker order --schema-file db/schema.sql -o -
smoker order --schema-file db/schema.sql --format json -o order.json
```

Text output marks steps inside a foreign-key cycle with `!` and shows
dependencies after `<-`.

## `smoker audit`

Produces a Markdown schema readiness audit. Reads metadata only, no rows.

| Option | Default | Description |
|---|---|---|
| `--dsn` | — | DSN or env var name |
| `--dsn-env` | `SMOKER_SOURCE_DSN` | Env var with the DSN |
| `--schema-file` | — | Build the catalog from a `.sql` dump instead of a live source |
| `--out`, `-o` | `audit.md` | Output file |

```bash
smoker audit --dsn-env SMOKER_SOURCE_DSN --out audit.md
```

The audit lists inventory, verifiable invariants, and risks by severity.

---

## `smoker profile`

Samples the source and saves per-column aggregates for distribution-aware
generation.

| Option | Default | Description |
|---|---|---|
| `--dsn` | — | DSN or env var name |
| `--dsn-env` | `SMOKER_SOURCE_DSN` | Env var with the DSN |
| `--sample` | `1000` | Rows sampled per table |
| `--sample-method` | `auto` | `auto`, `limit`, `system` or `bernoulli` |
| `--sample-seed` | `42` | Seed for `REPEATABLE` sampling |
| `--out`, `-o` | `profile.json` | Output file |
| `--no-values` | off | Omit top values for PII-sensitive schemas |

```bash
smoker profile --dsn-env SMOKER_SOURCE_DSN --sample 10000 -o profile.json
```

`auto` uses `LIMIT` below 50k rows and `TABLESAMPLE SYSTEM` above it. Partitioned
parents always use `LIMIT`, because PostgreSQL does not support `TABLESAMPLE` on
the parent. Dates, timestamps and times are stored as histogram buckets and
converted back during generation.

The profile stores null fractions, distinct counts, top values and 20-bucket
histograms. It is an artifact: keep it out of public repositories.

---

## `smoker plan`

Builds a generation plan without touching data.

| Option | Default | Description |
|---|---|---|
| `--dsn` | — | DSN or env var name |
| `--dsn-env` | `SMOKER_SOURCE_DSN` | Env var with the DSN |
| `--rows` | `1000` | Default rows per table |
| `--seed` | `42` | Deterministic seed |
| `--table-rows` | — | Per-table overrides: `users=5000,plans=5` |
| `--out`, `-o` | `plan.json` | Output file |

```bash
smoker plan --rows 10000 --table-rows "billing.plans=10" -o plan.json
```

Prints warnings for foreign-key cycles and tables without a primary key.

---

## `smoker generate`

Generates rows into the target database, or into a `.sql` file. Does not verify.

| Option | Default | Description |
|---|---|---|
| `--source` | — | Source DSN or env var name |
| `--source-env` | `SMOKER_SOURCE_DSN` | Source env var |
| `--target` | — | Target DSN or env var name |
| `--target-env` | `SMOKER_TARGET_DSN` | Target env var |
| `--schema-file` | — | Build the catalog from a `.sql` dump; the source DSN becomes optional |
| `--sql` | — | Write `INSERT` statements to this file instead of a target database |
| `--rows` | `1000` | Default rows per table |
| `--seed` | `42` | Deterministic seed |
| `--table-rows` | — | Per-table overrides |
| `--truncate/--no-truncate` | truncate | Truncate the target first, or emit `TRUNCATE` in the SQL file |
| `--method` | `copy` | `copy`, `insert` or `bulk` (SQL Server on-prem) |
| `--bulk-dir` | — | Server-visible directory for `BULK INSERT` |
| `--bulk-local-dir` | — | Client directory mapped to `--bulk-dir` |
| `--temporal-history` | off | Update temporal tables to create history rows |
| `--catalog` | — | Reuse a saved catalog JSON |
| `--profile` | — | Profile JSON from `smoker profile` |

```bash
smoker generate --rows 5000 --seed 42 --profile profile.json
smoker generate --schema-file db/schema.sql --rows 1000 --sql data.sql
psql -d target -f data.sql
```

SQL export writes `BEGIN`, optional batched `TRUNCATE`, batched `INSERT`
statements, `setval` calls for identity columns and `COMMIT`. The target schema
must already exist. Self-referencing tables fall back to `COPY` only in database
mode; SQL export always uses `INSERT`.

---

## `smoker verify`

Verifies invariants against an existing dataset. Does not generate.

| Option | Default | Description |
|---|---|---|
| `--dsn` | — | DSN or env var name |
| `--dsn-env` | `SMOKER_TARGET_DSN` | Env var with the DSN |
| `--schema-file` | — | Build the catalog from a `.sql` dump; the target DSN is still required |
| `--spec` | — | YAML with application rules |
| `--out`, `-o` | `proof` | Output directory |
| `--sample` | `5` | Rows shown per failure |
| `--jobs` | `1` | Parallel verification connections |
| `--seed` | `42` | Seed recorded in the report |
| `--catalog` | — | Reuse a saved catalog JSON |
| `--format` | `json,html,junit` | Report formats |
| `--fail-on` | `fail` | `never`, `error` or `fail` |

```bash
smoker verify --spec db/plan.yml --format json,html --fail-on error
```

---

## `smoker run`

Introspects, generates, repairs, verifies and writes reports. This is the
command most teams use.

It accepts every option from `generate` and `verify`, plus:

| Option | Default | Description |
|---|---|---|
| `--max-passes` | `3` | Repair fixed-point limit |
| `--schema-file` | — | Build the catalog from a `.sql` dump; the source DSN becomes optional |
| `--profile` | — | Profile JSON |
| `--format` | `json,html,junit` | Report formats |
| `--fail-on` | `fail` | Exit policy |
| `--temporal-history` | off | Update temporal tables to create history rows |
| `--jobs` | `1` | Parallel verification connections |
| `--method bulk` | — | Use `BULK INSERT` (on-prem SQL Server only) |
| `--bulk-dir` / `--bulk-local-dir` | — | Server / client directories for `BULK INSERT` |

```bash
smoker run \
  --rows 5000 \
  --seed 42 \
  --spec db/plan.yml \
  --profile profile.json \
  --max-passes 3 \
  --fail-on never \
  --out proof
```

---

## `--fail-on` policy

| Policy | Behavior | When to use |
|---|---|---|
| `never` | Always exit 0; still write the report | Shadow mode during adoption |
| `error` | Exit 1 only if verification could not run | Baselines with known violations |
| `fail` | Exit 1 on any violation or error | Normal CI |

## Reusing artifacts

```bash
smoker introspect -o catalog.json
smoker verify --catalog catalog.json
smoker run --catalog catalog.json
```

`--catalog` is supported by `verify` and `run`. `plan` currently introspects the
database; passing `--catalog` to it is a v0.3 improvement.
