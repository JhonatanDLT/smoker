# FAQ

### Is this an LLM?

No. pgproof reads `pg_catalog`, extracts invariants, generates rows
deterministically and verifies them with SQL. There is no model in the critical
path. A future version may use an LLM to suggest semantic names for opaque
columns, but never to decide correctness.

### Does it copy production data?

No. It reads schema metadata and, optionally, aggregates from a sample
(`pgproof profile`). The generated rows are synthetic. Categorical values from
the profile can come from production; use `--no-values` if that matters.

### Which databases are supported?

PostgreSQL 14–17 today. SQL Server is next, then MySQL and Oracle. The adapter
interface already exists in `core/ports.py`.

### Does it handle triggers?

It inventories them and reports them as `skipped`. Their side effects are not
replayed. If a trigger writes to an audit table, that table must be generated
explicitly.

### Does it handle partitioned tables?

Partially. Partitioned parents are introspected, but partition routing is not
implemented yet. It is the next correctness gap after CI.

### Can it guarantee that my application rules hold?

It can verify any rule you declare in the spec. It can repair a rule only when
you provide a `repair:` statement. It does not invert arbitrary SQL.

### How is it different from Faker?

Faker generates plausible values. pgproof generates values that satisfy the
schema and then **proves it** with a report. The report is the product.

### How is it different from Tonic or Delphix?

Those are enterprise platforms sold to compliance teams at $18k–$250k/year.
pgproof is a local CLI with an open core, priced for a single team, and it
specializes in constraint satisfaction and verification.

### Does it work offline?

Yes. The CLI has no telemetry and makes no network calls. You only need network
access to the database.

### Can I use it for ML training data?

Partially. It generates tabular data with marginal distributions. Correlations
between columns are not modelled yet, so it is not a replacement for a synthetic
data platform focused on ML.

### Is the generated data safe to commit?

No. Treat `proof/`, `catalog.json` and `profile.json` as internal artifacts.
They contain schema names and synthetic samples.

### How do I sell this?

See [the pilot playbook](pilot/README.md). The short version: sell the audit,
then a two-week pilot, then a monthly plan.

### Why is the report so strict about what it does not verify?

Because a certificate that hides its gaps is worse than no certificate. The
report lists triggers, exclusion constraints, cross-table rules and anything
else that was not checked.
