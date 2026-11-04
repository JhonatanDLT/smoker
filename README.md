# pgproof

**Synthetic Postgres data that ships with proof.**

`pgproof` reads your PostgreSQL schema, generates deterministic rows, and then
**verifies every invariant it can find** against the generated dataset. The
output is a report you can hand to a reviewer, a tech lead, or an auditor —
including an explicit list of what it could **not** verify.

It is not a Faker wrapper. Faker produces plausible values; pgproof produces
rows that satisfy the schema and proves it.

---

## Why

Copying production into staging is a legal and operational risk. Faker-style
seeds produce data that looks real but violates foreign keys, checks, derived
columns, and enum domains. The failure shows up later, as a flaky test or a
broken demo.

pgproof treats the schema as the specification:

1. **Introspect** — read `pg_catalog` (not a dump).
2. **Extract** — turn constraints, enums, triggers and checks into invariants.
3. **Generate** — deterministic rows (same seed, same bytes).
4. **Verify** — run each invariant as SQL and count violations.
5. **Certify** — JSON + HTML report with coverage and limitations.

---

## Install

Until the first public release, install from source:

```bash
git clone <repo> pgproof
cd pgproof
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pgproof version
```

Once published:

```bash
pipx install pgproof
# or
pip install pgproof
```

Requires Python 3.11+ and PostgreSQL 14–17.

---

## Quickstart

```bash
createdb pgproof_demo
psql -d pgproof_demo -f examples/saas-billing/schema.sql

export PGPROOF_SOURCE_DSN=postgresql:///pgproof_demo
export PGPROOF_TARGET_DSN=postgresql:///pgproof_demo

pgproof run \
  --rows 200 \
  --seed 42 \
  --spec examples/saas-billing/plan.yml \
  --out proof

open proof/report.html
```

Expected output:

```
pgproof 0.2.1 · 10 tables · 2,000 rows · seed 42
  generate billing.accounts        200 rows
  generate billing.plans           200 rows
  ...
RESULT PASS · 63 passed · 0 failed · 0 errors · 2 skipped · 0 violations
```

---

## Commands

| Command | What it does |
|---|---|
| `pgproof introspect` | Save the catalog as JSON |
| `pgproof audit` | Produce a Markdown schema readiness audit |
| `pgproof profile` | Sample the source and save a distribution profile |
| `pgproof plan` | Build a generation plan without touching data |
| `pgproof generate` | Generate rows into a target database |
| `pgproof verify` | Verify invariants and write a report |
| `pgproof run` | Introspect, generate, repair, verify and report |

Common options:

```
--source, --target     DSN or the name of an environment variable
--rows                 default rows per table (default: 1000)
--table-rows           per-table overrides, e.g. users=5000,plans=5
--seed                 deterministic seed (default: 42)
--spec                 YAML file with application-level rules and repairs
--profile              profile JSON from `pgproof profile`
--out                  output directory (default: proof)
--format               json,html,junit (default: all three)
--fail-on              never, error, fail (default: fail)
--max-passes           repair fixed-point limit (default: 3)
--truncate/--no-truncate
```

`pgproof` exits `1` when the report verdict is `FAIL`, so it drops straight into
CI.

---

## What it verifies

- `NOT NULL`, `UNIQUE`, primary keys
- Foreign keys, including composite keys
- `CHECK` constraints, including arithmetic and column-to-column comparisons
- Enum domains
- Composite unique keys
- Application rules declared in `--spec`

## Fixtures
Three public schemas exercise different features:

| Fixture | What it covers |
|---|---|
| `examples/saas-billing` | Enums, self-reference, trigger inventory, derived column |
| `examples/ecommerce` | Unique `varchar` truncation, ranges, nullable ordering |
| `examples/multi-tenant-crm` | Composite keys, scope-aware tenant foreign keys |
| `examples/analytics` | JSONB, bounded decimals, timestamp ordering |

```bash
make fixtures   # creates one database per fixture and writes proof/<fixture>/
```

## Errors

A failed load names the table, constraint, row and values:

```
load failed in debug.bad
  reason:     new row for relation "bad" violates check constraint "name_fixed"
  constraint: name_fixed
  sqlstate:   23514
  row:        1
  values:     id=1, name='Jorge Flores'
  detail:     Failing row contains (1, Jorge Flores).
```

`pgproof` exits `2` on a load error and `1` on a verification failure.

## Audit

Before generating anything, get a one-page assessment of the schema:

```bash
pgproof audit --dsn-env PGPROOF_SOURCE_DSN --out audit.md
```

It lists the inventory, the invariants pgproof would verify, and risks by
severity: missing primary keys, orphan-prone tables, triggers, unparsed checks
and `NOT VALID` constraints. It reads metadata only.

## Repairs

A cross-table rule can declare how to fix itself:

```yaml
- id: invoice_total_matches_lines
  table: billing.invoices
  description: invoice total equals the sum of its lines
  sql: |
    SELECT i.id FROM billing.invoices i
    LEFT JOIN (SELECT invoice_id, SUM(amount) t FROM billing.invoice_lines GROUP BY 1) l
      ON l.invoice_id = i.id
    WHERE i.total <> COALESCE(l.t, 0)
  repair: |
    UPDATE billing.invoices i
    SET total = COALESCE((
      SELECT SUM(l.amount) FROM billing.invoice_lines l WHERE l.invoice_id = i.id
    ), 0)
```

The pipeline becomes **generate → repair → verify**. It runs up to
`--max-passes` times, stops when it converges, and recomputes the dataset hash
after the repair. The report lists every action with before/after counts.

```bash
pgproof run --spec examples/saas-billing/plan-repair.yml --out proof
# repairs: 50 rows across 1 actions in 1 pass(es), residual 0
```

## Distributions

`pgproof profile` samples the source and stores per-column aggregates: null
fraction, distinct count, top values and numeric histograms. `--profile` feeds
them back into the generator, so the synthetic data keeps the source's skew
without copying a single real row.

```bash
pgproof profile --dsn-env PGPROOF_SOURCE_DSN --sample 10000 -o profile.json
pgproof run --profile profile.json --rows 5000 --out proof
```

Constraint correctness always wins: a sampled value still passes through the
local rule engine and the database.

## CI and exit codes

- `report.xml` is JUnit, ready for GitHub Actions, GitLab, Jenkins or Buildkite.
- `--fail-on never` runs in shadow mode: the report is written, the build
  passes. Flip to `fail` when the team trusts the baseline.
- `--fail-on error` fails only when verification could not run.
- Exit codes: `0` pass, `1` verification failure, `2` load or configuration
  error.

## What it does not verify (yet)

- Trigger side effects. Triggers are inventoried and reported as `skipped`.
- Exclusion constraints.
- Cross-table business rules such as `invoice.total = SUM(lines.amount)`.
- Statistical fidelity beyond the sampled profile.

The report always lists these limitations. **A certificate that hides its gaps
is worse than no certificate.**

---

## How generation works

- `COPY` binary-safe text format for speed; row-by-row `INSERT` as a fallback.
- Primary keys are pre-allocated so foreign-key cycles still work.
- Child rows sample parent keys from pre-built pools: referential integrity is
  guaranteed by construction, not by retry.
- Local rules (`> 0`, `IN (...)`, `a = b * c`, `ends_at > starts_at`) are
  applied before the row reaches PostgreSQL.
- When a profile is provided, categorical and numeric distributions are sampled
  before semantic rules, never instead of constraint checks.
- Every table gets its own RNG seeded from `(seed, table)`.

---

## Architecture

```
cli ──► application ──► core (domain + ports)
                         ▲
                    adapters (Postgres, YAML, report)
```

The core has no database driver, no CLI and no I/O. That is what makes a second
dialect (SQL Server) an adapter, not a rewrite. The dependency rule is enforced
by `tests/test_architecture.py`.

See [`docs/architecture.md`](docs/architecture.md) for the ports and the SOLID
mapping.

---

## Documentation

- [Getting started](docs/getting-started.md)
- [CLI reference](docs/cli-reference.md)
- [Spec reference](docs/spec-reference.md)
- [Report reference](docs/report-reference.md)
- [CI integration](docs/ci.md)
- [Security](docs/security.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Architecture](docs/architecture.md)
- [Limitations](docs/limitations.md)
- [Pilot playbook](docs/pilot/README.md)
- [Full documentation index](docs/README.md)

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Integration tests need a live PostgreSQL:

```bash
export PGPROOF_TEST_DSN=postgresql:///pgproof_demo
.venv/bin/pytest -m integration
```

---

## License

Apache-2.0 for the core. The hosted verification service will be a separate
commercial offering.
