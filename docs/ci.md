# CI integration

## The adoption path

Do not turn on a hard failure on day one. Use shadow mode for two weeks:

1. Run with `--fail-on never`. The report is written, the pipeline stays green.
2. Review the report. Fix or declare the rules that matter.
3. Switch to `--fail-on fail`.

```bash
pgproof run --spec db/plan.yml --fail-on never --out proof
```

## GitHub Actions

The repository ships a composite action at the root.

### Inputs

| Input | Required | Default |
|---|---|---|
| `source-dsn` | Yes | — |
| `target-dsn` | Yes | — |
| `rows` | No | `5000` |
| `seed` | No | `42` |
| `spec` | No | — |
| `profile` | No | — |
| `fail-on` | No | `fail` |
| `max-passes` | No | `3` |
| `format` | No | `json,html,junit` |
| `out` | No | `proof` |
| `version` | No | `source` |
| `python-version` | No | `3.12` |
| `upload-artifact` | No | `true` |
| `allow-same-db` | No | `false` |

`version: source` installs the checkout that contains the action. That is the
right value while pgproof is not published to PyPI. Once it is, set
`version: 0.9.9`.

### Outputs

| Output | Description |
|---|---|
| `verdict` | `PASS` or `FAIL` |
| `dataset-hash` | SHA-256 of the dataset |
| `report-path` | Path to the JSON report |

### Workflow

```yaml
name: Database integrity
on: [push, pull_request]

jobs:
  pgproof:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: app_test
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready --health-interval 10s
          --health-timeout 5s --health-retries 5
    env:
      PGPROOF_DSN: postgresql://postgres:postgres@localhost:5432/app_test
    steps:
      - uses: actions/checkout@v4
      - run: psql "$PGPROOF_DSN" -v ON_ERROR_STOP=1 -f db/schema.sql
      - uses: ./
        with:
          source-dsn: ${{ env.PGPROOF_DSN }}
          target-dsn: ${{ env.PGPROOF_DSN }}
          allow-same-db: "true"
          rows: "2000"
          spec: db/plan.yml
          fail-on: fail
```

The action:

1. Installs pgproof.
2. Runs generation and verification without echoing the DSNs.
3. Emits `::error` / `::warning` annotations for failures.
4. Appends a Markdown summary to `$GITHUB_STEP_SUMMARY`.
5. Uploads `proof/` as an artifact.
6. Fails the job if pgproof exited non-zero.

A full example is in `examples/github-actions/ci.yml`.

### SQL Server service container

The repository CI (`.github/workflows/ci.yml`) runs both PostgreSQL and SQL
Server. The SQL Server service needs the ODBC driver on the runner:

```yaml
services:
  mssql:
    image: mcr.microsoft.com/mssql/server:2022-latest
    env:
      ACCEPT_EULA: "Y"
      MSSQL_SA_PASSWORD: "Pgproof!2026Strong"
    ports: ["1433:1433"]
    options: >-
      --health-cmd "/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P 'Pgproof!2026Strong' -C -Q 'SELECT 1'"
      --health-interval 10s --health-timeout 5s --health-retries 30

env:
  PGPROOF_MSSQL_DSN: "DRIVER={ODBC Driver 18 for SQL Server};SERVER=127.0.0.1,1433;DATABASE=master;UID=sa;PWD=Pgproof!2026Strong;TrustServerCertificate=yes;Encrypt=yes"
```

Install `msodbcsql18` from the Microsoft Ubuntu repository and run
`pip install -e ".[dev,sqlserver]"`. Then `pytest -m integration` exercises
both dialects. Azure SQL requires a real managed instance and is not part of
the default CI matrix; run a manual smoke test with `PGPROOF_AZURE_DSN`.

## GitLab CI

GitLab reads JUnit natively:

```yaml
pgproof:
  image: python:3.12
  services:
    - name: postgres:16
      alias: postgres
  variables:
    POSTGRES_PASSWORD: postgres
    PGPROOF_DSN: postgresql://postgres:postgres@postgres:5432/postgres
  script:
    - pip install pgproof
    - psql "$PGPROOF_DSN" -v ON_ERROR_STOP=1 -f db/schema.sql
    - >
      pgproof run
      --source "$PGPROOF_DSN"
      --target "$PGPROOF_DSN"
      --rows 2000
      --spec db/plan.yml
      --fail-on fail
      --out proof
  artifacts:
    when: always
    paths: [proof]
    reports:
      junit: proof/report.xml
```

## Jenkins

```groovy
pipeline {
  agent any
  stages {
    stage('pgproof') {
      steps {
        sh 'pip install pgproof'
        sh 'psql "$PGPROOF_DSN" -v ON_ERROR_STOP=1 -f db/schema.sql'
        sh 'pgproof run --rows 2000 --spec db/plan.yml --out proof'
      }
      post {
        always {
          junit 'proof/report.xml'
          archiveArtifacts artifacts: 'proof/**', allowEmptyArchive: true
        }
      }
    }
  }
}
```

## Ephemeral databases

The target must be disposable. Two patterns work:

### Service container (recommended)

Every job starts an empty PostgreSQL. The schema is loaded from a file. The
target disappears with the runner.

### Dedicated CI database

A long-lived `app_test` database that the job truncates. Set
`PGPROOF_TARGET_DSN` as a secret, not as a plain variable. pgproof truncates the
tables in the plan before writing.

## Artifacts

Keep `proof/report.json` for at least 30 days. It is the evidence. The HTML is
for humans; the XML is for the CI UI.

## What not to do

- Do not point the target at staging shared with humans during a deploy.
- Do not run two pipelines against the same target database concurrently.
- Do not pass the DSN as a command-line flag; it ends up in the report.
- Do not commit `profile.json` if it contains top values of sensitive columns.
