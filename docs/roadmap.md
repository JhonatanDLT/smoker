# Roadmap

The order is deliberate: each phase is funded by the previous one.

## v0.1 — Core (delivered)

- PostgreSQL 14–17 introspection.
- Invariants from constraints, enums and checks.
- Deterministic generation with pre-allocated keys and `COPY`.
- Local rules: ranges, `IN`, arithmetic derivations, column orderings, unique
  suffixes.
- YAML spec for application rules.
- JSON + HTML verification report.
- CLI: `introspect`, `plan`, `generate`, `verify`, `run`.
- Structured load errors: table, constraint, SQLSTATE, row and values.
- Four fixtures: SaaS billing, e-commerce, multi-tenant CRM, analytics.
- Clean architecture: ports, use cases and adapters, enforced by tests.

## v0.2 — Sellable (delivered)

- GitHub Action and JUnit XML output.
- `--fail-on` policy and exit codes documented.
- Better error messages: table, constraint, row, and the SQL that failed.
- Signed report digest and `--strict` mode. *(Digest delivered; signing pending.)*
- Four public fixtures: SaaS billing, e-commerce, multi-tenant CRM, analytics.
- Schema readiness audit (`pgproof audit`).
- `docker run` one-liner. *(pending)*

## v0.3 — Depth (in progress)

- Partitioned table routing (range, list, hash). **Delivered in 0.3.0.**
- `TABLESAMPLE` and date histograms for profiles. **Delivered in 0.3.1.**
- Column correlations. *(pending)*
- Sub-partitioning and multi-column partition keys. **Delivered in 0.3.2.**
- Schema include/exclude filters. **Delivered in 0.3.2.**
- Function-body deduplication by OID. **Delivered in 0.3.2.**
- Parallel verification and `TRUNCATE` batching. *(batching delivered in 0.2.1)*
- More rule shapes: `BETWEEN`, `OR`, `IS NULL` implications, `LIKE` patterns.
  *(partial)*
- `pgproof explain <invariant>`: why a rule exists and where it came from.

## v0.4 — SQL Server (months 4–6)

Only if two paying customers request it in writing.

- `synthadapters.mssql`: catalog introspection (`sys.*`), `BULK INSERT`, identity
  handling, `SET IDENTITY_INSERT`.
- SQL Server as a **source** for migration validation.
- SQL Server as a **target** for generation.
- Same invariant model, same report.

## v0.5 — Other dialects (months 7–9)

- MySQL / MariaDB.
- Oracle only with an enterprise contract that funds the work.
- A conformance suite that every adapter must pass.

## v1.0 — Static code analysis (months 10–12)

- Parse application SQL and ORM metadata to enrich the invariant set.
- Detect rules that live only in `WHERE` clauses, `JOIN`s and `IF`s.
- Rank candidate invariants by frequency and consistency across the codebase.
- Cross-validate candidates against real data before publishing them.
- New product surface: **migration impact analysis** ("what breaks if I change
  this column?"), which shares the entire engine.

## Commercial track (separate from the OSS core)

- Hosted verification service: upload a schema, get a report, pay per seat.
- Report archive and CI history.
- SSO, audit log, DPA.
- Not before 50 paying customers. The CLI is the distribution channel.

## Explicitly not planned

- A no-code UI for non-technical users.
- A general-purpose data masking suite.
- An LLM that writes schemas or decides eligibility.
- Marketplace of community workflows.
