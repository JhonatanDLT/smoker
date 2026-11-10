# Limitations and guarantees

This page exists because trust is built by publishing what you do **not** do.

## Guaranteed in v1

- Rows load into the target schema without violating `NOT NULL`, `UNIQUE`,
  primary keys, foreign keys, enum domains, and the `CHECK` constraints that the
  local rule parser understands.
- Composite foreign keys are supported.
- Partitioned tables are supported for range, list and hash keys, including
  multi-column keys and sub-partitioning. Introspection reads the partition
  tree, generation samples a leaf and stays inside every bound, and PostgreSQL
  routes each row.
- Trigger functions are stored once per OID, even when hundreds of triggers
  share the same function.
- Schema filters (`--schema`, `--include`, `--exclude`) restrict introspection
  without touching the database.
- Deep foreign-key chains are supported: the SCC pass is iterative, so a
  5,000-table chain plans without hitting Python's recursion limit.
- `TRUNCATE` runs in batches of 100 tables, keeping the lock footprint bounded
  on schemas with thousands of tables.
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
| Stored procedures and rules | not analyzed | Only trigger functions are read, and only their body is stored. `CREATE RULE` (`pg_rewrite`) and event triggers are not scanned. |
| Exclusion constraints (`EXCLUDE`) | `skipped` | Not generated against; PostgreSQL enforces them at load time. |
| Cross-table business rules | not enforced | `invoice.total = SUM(lines.amount)` requires a repair pass that v1 does not have. Declare it in the spec and it will be verified, not fixed. |
| `INSERT` mode with self-references | falls back to `COPY` | A row-by-row insert cannot reference a row that has not been inserted yet. |
| Distributions | sampled, optional | `TABLESAMPLE` above 50k rows; `LIMIT` below that and on partitioned parents. Independent per column. |
| Repairs | explicit only | A rule is repaired only when the spec declares `repair:`. |
| Row-level security policies | ignored | Generation uses a privileged connection. |
| Domain constraints | partially | The base type is generated; domain `CHECK`s are enforced by PostgreSQL at load time. |
| `NOT VALID` constraints | verified, flagged | They are enforced on new rows, so we check them and warn that old rows were never validated. |
| Partitioned tables | **supported** | Range, list and hash keys, including multi-column keys and sub-partitioning. Partitions are introspected as a tree, not as separate tables. |
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
- Verification runs one query per invariant. It is fast for thousands of
  invariants and linear after that; parallel verification is a v0.3 concern.
- v1 is single-process. Parallel table generation is a v0.3 concern.
- The tool requires a live PostgreSQL connection for anything beyond parsing a
  saved catalog.

## What the report never claims

- That the data is statistically identical to production.
- That it is safe for regulated workloads without review.
- That it covers triggers or application code.

It reports coverage, violations, skipped items and limitations. Nothing else.

## Dump loading limits

- `COPY ... FROM stdin` data is skipped. Only the schema is loaded.
- PL/pgSQL and PL/SQL bodies are stored and scanned for dynamic SQL, `IF` and
  `CASE` conditions, and write/read targets. They are not interpreted; triggers
  are not replayed.
- Only simple trigger/function rules become enforced checks: comparisons,
  `IN`, `IS NOT NULL`, `BETWEEN`, `LIKE`, length limits and column-to-column
  assignments. Compound conditions (`AND`/`OR`), unknown functions,
  `now()`/`random()` and self-referential assignments are reported as
  candidates with lower confidence.
- Rules derived from a plain function (not used by a trigger) have no table
  target and are reported but never applied.
- Rule extraction runs on the offline dump loader. Live introspection still
  inventories functions and triggers but does not derive rules from their
  bodies.
- `CREATE RULE`, event triggers and RLS policies are stored as raw objects but
  not applied.
- `CREATE TABLE ... AS SELECT` is stored raw and not expanded.
- `CREATE TABLE ... OF <composite type>` is not expanded; the composite type is
  stored raw.
- Inferred foreign keys are heuristic. Inspect `ForeignKey.inferred` before
  trusting them; a wrong inference makes the child reference the wrong parent.
- Trigger `WHEN` clauses and `UPDATE OF` column lists are captured, but the
  generator does not evaluate them.
- `NOT VALID` is captured; deferrable constraints are not.
- Only simple unique indexes become unique constraints; partial and expression
  indexes are stored as raw objects.
- `ALTER TABLE ... ATTACH PARTITION` is processed; `DETACH PARTITION` and
  `DETACH PARTITION ... CONCURRENTLY` are not.

## Distribution and repair limits

- Profiles use `TABLESAMPLE SYSTEM` above 50k rows and `LIMIT` below that.
  `BERNOULLI` is available but slower. Partitioned parents always use `LIMIT`.
- Date and timestamp histograms are bucketed on a numeric axis; `timestamptz`
  is normalized to UTC.
- Correlations between columns (country ↔ city, price ↔ category) are not
  modelled. Each distribution is sampled independently.
- `top_values` can contain personally identifiable information. Use
  `pgproof profile --no-values` for sensitive schemas, and keep the profile file
  out of the repository.
- Repairs are explicit SQL. pgproof does not invert arbitrary application rules
  automatically.
- Repairs run at most `--max-passes` times. A rule that does not converge is
  reported as residual; it is never retried forever.
