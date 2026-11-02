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
pgproof 0.1.0 · 10 tables · 2,000 rows · seed 42
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
| `pgproof plan` | Build a generation plan without touching data |
| `pgproof generate` | Generate rows into a target database |
| `pgproof verify` | Verify invariants and write a report |
| `pgproof run` | All of the above, end to end |

Common options:

```
--source, --target     DSN or the name of an environment variable
--rows                 default rows per table (default: 1000)
--table-rows           per-table overrides, e.g. users=5000,plans=5
--seed                 deterministic seed (default: 42)
--spec                 YAML file with application-level rules
--out                  output directory (default: proof)
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
