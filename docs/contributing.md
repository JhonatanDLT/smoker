# Contributing

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/ruff check src tests scripts
```

Integration tests need a live PostgreSQL:

```bash
createdb smoker_demo
psql -d smoker_demo -f examples/saas-billing/schema.sql
SMOKER_TEST_DSN=postgresql:///smoker_demo .venv/bin/pytest -m integration
```

## The dependency rule

```
cli ──► application ──► core (domain + ports)
                         ▲
                    adapters (Postgres, YAML, report)
```

- `core` may only import the standard library.
- `application` may import `core`, never `adapters`.
- `adapters` implement ports and may import `core`.
- `cli` is the only place that constructs concrete adapters.

`tests/test_architecture.py` enforces this. If your change needs to break the
rule, the design is wrong, not the test.

## Where does a change go?

| Change | Layer |
|---|---|
| New invariant type | `core/invariants.py` + tests |
| New generation rule | `core/invariants.py` (parse) + `core/generator.py` (apply) |
| New report format | `adapters/report/` (one class) |
| New spec format | `adapters/spec/` (one `SpecLoader`) |
| New database | `adapters/<dialect>/` implementing the ports |
| New CLI command | `application/` use case + `cli.py` wiring |

## Adding a renderer

1. Implement `extension` and `render(report)`.
2. Register it in `cli._REPORT_RENDERERS`.
3. Add a test that parses the output and checks the counts against
   `report.summary()`.

Do not touch `core/report.py`.

## Adding a dialect

Implement the ports in `core/ports.py`:

- `CatalogReader`
- `RowWriter`
- `QueryRunner`
- `SqlExecutor`

Then add a factory in the CLI. The core should not need a single change. If it
does, the port is incomplete.

## Format compatibility

- `report.json` has `format_version`. Bump it for breaking changes and document
  the migration.
- The spec is additive: new keys are optional.
- The CLI follows semantic versioning.

## Fixtures

Every new feature needs a fixture that exercises it:

```
examples/<name>/schema.sql
examples/<name>/plan.yml
```

Add it to `tests/test_fixtures.py` and `scripts/run_fixtures.sh`.

## Commit style

```
<area>: <imperative summary>

Optional body explaining why.
```

Examples:

- `generator: enforce composite unique keys`
- `report: add JUnit XML renderer`
- `repair: stop at a fixed point`

## Pull request checklist

- [ ] Tests pass, including integration.
- [ ] `ruff check src tests scripts` is clean.
- [ ] The architecture test passes.
- [ ] New behavior is documented.
- [ ] No DSN, real data or secrets in fixtures or reports.
- [ ] `STATUS.md` updated if the capability table changed.
