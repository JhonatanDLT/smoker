# Limitations and guarantees

This page exists because trust is built by publishing what you do **not** do.

## Guaranteed in v1

- Rows load into the target schema without violating `NOT NULL`, `UNIQUE`,
  primary keys, foreign keys, enum domains, and the `CHECK` constraints that the
  local rule parser understands.
- Composite foreign keys are supported.
- Composite unique keys are supported: a text column in the key receives a
  deterministic suffix when a collision appears.
- Scope-aware foreign keys are supported when the scope column is a foreign key
  in both tables (for example `tenant_id` → `tenants`).
- Foreign-key cycles are supported through pre-allocated keys.
- Generation is deterministic: the same seed and schema produce the same rows.
- The report includes a SHA-256 of the generated data.
- The verifier runs independently of the generator.

## Not verified in v1

| Area | Status | Why |
|---|---|---|
| Trigger side effects | `skipped` | We inventory triggers but do not replay them. A trigger that inserts into an audit table will not be simulated. |
| Exclusion constraints (`EXCLUDE`) | `skipped` | Not generated against; PostgreSQL enforces them at load time. |
| Cross-table business rules | not enforced | `invoice.total = SUM(lines.amount)` requires a repair pass that v1 does not have. Declare it in the spec and it will be verified, not fixed. |
| `INSERT` mode with self-references | falls back to `COPY` | A row-by-row insert cannot reference a row that has not been inserted yet. |
| Row-level security policies | ignored | Generation uses a privileged connection. |
| Domain constraints | partially | The base type is generated; domain `CHECK`s are enforced by PostgreSQL at load time. |
| `NOT VALID` constraints | verified, flagged | They are enforced on new rows, so we check them and warn that old rows were never validated. |
| Partitioned tables | partial | The parent is introspected; routing depends on the partition key. Large partition sets are not optimized. |
| Generated columns | excluded from writes | The database computes them. |
| Arrays | empty array | Element type and length are not modelled. |
| `bytea` | placeholder | Values are not meaningful. |
| Statistical fidelity | sampled | Profiles come from `LIMIT n`, not the full table. |
| Unique constraints with `NULL`s | partial | PostgreSQL treats `NULL`s as distinct; the verifier mirrors that. |
| Scope-aware foreign keys | partial | Detected only when the scope column is a foreign key in both tables. |
| `TRUNCATE ... CASCADE` | warning | It can truncate tables outside the plan if they reference the target. |

## Operational limits

- `--rows` is per table. Very large tables need `--table-rows` and enough disk.
- Key pools use one tuple per row per key. A 10M-row table with a UUID primary
  key will use hundreds of MB before generating a single row.
- v1 is single-process. Parallel table generation is a v0.3 concern.
- The tool requires a live PostgreSQL connection for anything beyond parsing a
  saved catalog.

## What the report never claims

- That the data is statistically identical to production.
- That it is safe for regulated workloads without review.
- That it covers triggers or application code.

It reports coverage, violations, skipped items and limitations. Nothing else.
