# Azure SQL

Azure SQL Database is SQL Server as a managed service. The `azuresql` dialect
uses the same engine adapter as `sqlserver` with stricter capabilities.

## Connection

```bash
pip install "pgproof[sqlserver]"

export PGPROOF_AZURE_DSN="DRIVER={ODBC Driver 18 for SQL Server};SERVER=my-server.database.windows.net;DATABASE=app;UID=appuser;PWD=...;Encrypt=yes;TrustServerCertificate=no"
```

Microsoft Entra ID is passed through the ODBC connection string:

```text
Authentication=ActiveDirectoryPassword;UID=user@tenant;PWD=...
Authentication=ActiveDirectoryServicePrincipal;UID=<client-id>;PWD=<secret>
```

## Commands

```bash
pgproof introspect --dialect azuresql --dsn-env PGPROOF_AZURE_DSN -o catalog.json
pgproof verify     --dialect azuresql --dsn-env PGPROOF_AZURE_DSN --out proof
pgproof generate   --dialect azuresql --source-env PGPROOF_AZURE_DSN --target-env PGPROOF_AZURE_DSN
```

`--databases` accepts only the connected database for Azure SQL. Cross-database
catalogs are rejected.

## Capabilities

`AzureSqlDialect` sets:

- `cross_database=False`: no `USE`, no `[other_db].[schema].[table]`.
- `managed_service=True`.
- `bulk_insert_from_file=False`: use `fast_executemany`, not `BULK INSERT`.
- Transient connection retries for serverless auto-pause and failover
  SQLSTATES (`40613`, `40197`, `40501`, `49918`, `49919`, `49920`, `08001`,
  `08S01`).

## Limitations

- Azure SQL is not part of the default CI matrix; use a free tier or a
  designated test database for a smoke test.
- T-SQL dumps containing `USE` are rejected by the `azuresql` DDL loader.
- Temporal history and `BULK INSERT` are not implemented.
- Cross-database views/procedures are a SQL Server on-prem feature.

## Multi-database on SQL Server

For an on-prem SQL Server, `--databases` builds one catalog with
`database.schema.table` keys:

```bash
pgproof introspect --dialect sqlserver --dsn-env PGPROOF_MSSQL_DSN \
  --databases app,audit -o catalog.json

pgproof generate --dialect sqlserver \
  --source-env PGPROOF_MSSQL_DSN --target-env PGPROOF_MSSQL_DSN \
  --databases app,audit --rows 1000
```

The writer, query runner, profiler and SQL-script exporter qualify tables with
three-part names. Cross-database joins in views and inferred `*_id`
relationships are preserved.
