# Architecture

## Principles

1. **The core is pure.** `pgproof.core` has no database driver, no CLI and no
   I/O. It can be tested with dataclasses and unit tests.
2. **The catalog is the source of truth.** We read `pg_catalog`, not a dump.
   Parsing DDL is a fallback for when no live database exists.
3. **One invariant, two compilations.** Every invariant becomes a SQL predicate
   for verification and a generation rule for the planner. They cannot diverge
   because they share the same source.
4. **The verifier never trusts the generator.** It receives a connection and a
   list of invariants, and counts violations independently.

```
┌─────────────────────────────┐
│            cli.py           │  Typer commands
└──────────────┬──────────────┘
               │
┌──────────────▼──────────────┐
│           core/             │  pure Python
│  ir · graph · invariants    │
│  planner · generator        │
│  verifier · report          │
└──────────────▲──────────────┘
               │
┌──────────────┴──────────────┐
│     adapters/postgres/      │  psycopg3
│  introspect · profile       │
│  writer                     │
└─────────────────────────────┘
```

`adapters/base.py` defines the `DialectAdapter` protocol. SQL Server and MySQL
will implement it without touching `core`.

---

## Data flow

```
1. introspect(conn)              → Catalog
2. build_plan(catalog, rows)     → Plan (topological order, row budgets)
3. Generator(catalog, plan)      → row streams
4. write_table(conn, table, …)   → COPY into the target
5. invariants_from_catalog()     → [Invariant]
   + invariants_from_spec()      → application rules
6. verify_all(conn, invariants)  → Verification
7. build_report(…)               → report.json + report.html
```

---

## Introspection

We query the catalog directly:

| Need | Source |
|---|---|
| Tables and partitions | `pg_class` (`relkind` in `r`, `p`) |
| Columns, defaults | `pg_attribute`, `pg_attrdef`, `pg_get_expr` |
| Types and enums | `pg_type`, `pg_enum` |
| Constraints | `pg_constraint`, `pg_get_constraintdef` |
| Triggers and functions | `pg_trigger`, `pg_proc`, `pg_get_functiondef` |
| Sequences | `pg_get_serial_sequence`, `pg_sequence` |
| Statistics | `pg_stats` (used by the profiler) |

`pg_get_constraintdef` returns normalized SQL such as:

```
CHECK (amount = (quantity::numeric * unit_price))
```

The predicate extractor strips the `CHECK (...)` wrapper, and the rule parser
tolerates casts, parentheses and `NOT VALID`.

---

## Invariants

Every invariant carries a `violation_sql` that returns offending rows:

| Kind | Violation query |
|---|---|
| `not_null` | `SELECT 1 FROM t WHERE col IS NULL` |
| `unique` | `GROUP BY cols HAVING count(*) > 1` |
| `foreign_key` | anti-join against the parent table |
| `check` | `WHERE (predicate) IS FALSE` |
| `enum` | `WHERE col::text NOT IN (...)` |
| `app_rule` | user-provided SQL from the spec |

`WHERE (predicate) IS FALSE` is deliberate: SQL checks pass on `NULL`, and
`NOT (NULL)` is `NULL`, which would silently skip violations.

---

## Generation

### Pre-allocated keys

Before generating any row, the generator builds a pool of primary-key values for
every table and every unique key. This solves foreign-key cycles without
recursion: a child can reference a parent that has not been generated yet,
because the parent's keys already exist.

### Topological order

`graph.py` builds child → parent edges and condenses strongly connected
components with Tarjan. The order is parents first; cycles are collapsed and
reported as warnings.

### Row construction

1. Assign primary-key values from the pool.
2. Sample foreign-key values from the parent pool (4% `NULL` when nullable).
3. Generate the remaining columns from the semantic type and SQL type.
4. Apply derived rules (`amount = quantity * unit_price`).
5. Apply ordering rules (`ends_at > starts_at`).
6. Apply unique suffixes after truncation, so `varchar(16)` unique columns do
   not collide.
7. Validate against local rules; retry up to 50 times with fresh values.

### Determinism

Each table has its own `random.Random` seeded with `f"{seed}:{table}"`. Retries
consume the same stream in the same order. Two runs with the same seed produce
byte-identical rows, and the report includes a SHA-256 of the generated data.

---

## Verification

| Level | What it checks |
|---|---|
| L0 | Structural: constraints exist and are validated |
| L1 | Every extracted invariant, as a violation query |
| L2 | Row counts and per-table totals |
| L3 | Application rules from the spec |
| L4 | Reproducibility: dataset hash |

L2 and L3 are partial in v1; the report states exactly what was not checked.

---

## Report

The JSON is canonical. The HTML is a rendering. Both include:

- Verdict (`PASS` / `FAIL`).
- Per-invariant status, violation count, SQL and a sample of offending rows.
- Skipped invariants and the reason.
- Limitations.
- The exact command to reproduce the run.
- Schema hash and dataset hash.

A certificate that hides its gaps is worse than no certificate.

---

## Multi-dialect roadmap

```python
class DialectAdapter(Protocol):
    name: str
    def introspect(self, conn) -> Catalog: ...
    def profile(self, conn, table, sample) -> Any: ...
    def write(self, conn, table, rows, method) -> int: ...
    def capabilities(self) -> Capabilities: ...
```

`Capabilities` describes what the engine supports (`deferrable_fk`,
`generated_columns`, `identity_columns`, `exclusion_constraints`,
`disable_triggers`). Unsupported features degrade to `skipped`, never to a
silent exception.

SQL Server is next because migration projects from SQL Server to Postgres have
the highest willingness to pay.

---

## Security

- Credentials come from the environment or a `--dsn` flag. They are never
  written to the report, the catalog or the plan.
- Real samples are held in memory only; the profiler keeps aggregates.
- No telemetry. The CLI works fully offline.
- The target must be a different database unless `--force` is passed (planned).
- The generated data is synthetic: no production value is copied.

---

## Capacity

`COPY` handles 50–150k rows/s on simple tables; Python generation is the
bottleneck. On a 4-vCPU / 24 GB VPS:

- 1M rows across 10 tables: seconds to a couple of minutes, depending on checks.
- Memory is bounded by the key pools (one tuple per row per key), not by the
  row data, which is streamed to `COPY`.
