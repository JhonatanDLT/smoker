# Realistic schema pack

Six domain schemas designed to exercise the parts of smoker that matter in
production: multiple schemas, composite keys, nullable and cyclic relationships,
partial/filtered unique indexes, `EXCLUDE` ranges, partitions, temporal history,
identity columns, triggers, views and stored procedures.

Each domain ships a PostgreSQL DDL and a SQL Server DDL. The tables are
comparable across engines, but advanced constraints use the native mechanism:

| Domain | Schema | PostgreSQL highlights | SQL Server highlights |
|---|---|---|---|
| `saas-billing` | `billing` | enums, `EXCLUDE` on daterange, partial unique, partitions, trigger | filtered unique, temporal invoices, partitions, trigger, procedure |
| `ecommerce` | `shop` | enums, partial unique on boolean, self-FK, partitions | filtered unique, temporal orders, one-to-one FK, partitions |
| `fintech` | `fintech` | `EXCLUDE` on tstzrange, money checks, partitions | temporal accounts, multiple FKs to `accounts`, partitions |
| `logistics` | `logistics` | composite PK, many FKs, partitions | temporal shipments, partitions, composite keys |
| `multi-tenant-crm` | `crm` | tenant-scoped FKs, composite uniqueness | temporal deals, tenant-scoped FKs, composite keys |
| `healthcare` | `health` | `EXCLUDE` on appointment periods, one-to-one FK, partitions | temporal encounters, one-to-one FK, partitions |

## Requirements

- PostgreSQL 16 reachable through `psql`/`dropdb`/`createdb`.
- SQL Server 2022 in Docker with ODBC Driver 18 and `sqlcmd` in the container.
- The repository virtualenv (`make install`) with the `sqlserver` extra:
  `pip install -e ".[dev,sqlserver]"`.

## Run everything

```bash
export SMOKER_MSSQL_PASSWORD='Smoker!2026Strong'
./scripts/run_realistic.sh
```

Reports land in `proof/realistic/<domain>/<dialect>/`.

Useful environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `ROWS` | `500` | Rows per table |
| `SEED` | `42` | Deterministic seed |
| `SMOKER_MSSQL_CONTAINER` | `smoker-mssql` | Docker container with SQL Server |
| `SMOKER_MSSQL_PASSWORD` | `Smoker!2026Strong` | SA password inside the container |
| `SMOKER_MSSQL_SERVER` | `127.0.0.1,14333` | Host/port for the DSN |

PostgreSQL databases are named `smoker_real_<domain>` and SQL Server databases
`smoker_real_<domain>` as well.

## Run one domain manually

PostgreSQL:

```bash
dropdb --if-exists smoker_real_billing
createdb smoker_real_billing
psql -d smoker_real_billing -f examples/realistic/saas-billing/postgres/schema.sql
.venv/bin/smoker run \
  --source postgresql:///smoker_real_billing \
  --target postgresql:///smoker_real_billing \
  --schema billing --rows 500 --seed 42 \
  --out proof/realistic/saas-billing/postgres
```

SQL Server:

```bash
docker exec smoker-mssql /opt/mssql-tools18/bin/sqlcmd \
  -S localhost -U sa -P "$SMOKER_MSSQL_PASSWORD" -C \
  -Q "DROP DATABASE IF EXISTS smoker_real_billing; CREATE DATABASE smoker_real_billing;"
docker exec -i smoker-mssql /opt/mssql-tools18/bin/sqlcmd \
  -S localhost -U sa -P "$SMOKER_MSSQL_PASSWORD" -C -d smoker_real_billing \
  < examples/realistic/saas-billing/sqlserver/schema.sql
.venv/bin/smoker run \
  --dialect sqlserver \
  --source "$SMOKER_MSSQL_DSN" --target "$SMOKER_MSSQL_DSN" \
  --schema billing --rows 500 --seed 42 --temporal-history \
  --out proof/realistic/saas-billing/sqlserver
```

## Notes and known differences

- Audit tables live in a separate `audit` schema. Run smoker with
  `--schema <domain>` so synthetic rows are not inserted into audit tables;
  triggers still write there.
- PostgreSQL self-referencing FKs work because `COPY` checks the constraint at
  the end of the statement. SQL Server checks row by row for some access paths,
  so the equivalent schemas avoid forward self-references.
- SQL Server temporal tables are updated by `--temporal-history`; PostgreSQL
  uses audit triggers instead.
- `EXCLUDE` does not exist in SQL Server, so the SQL Server versions use
  filtered unique indexes plus explicit `CHECK` constraints.
- Tenant-scoped foreign keys are valid, but cross-tenant consistency is
  best-effort when a tenant has no row in the referenced dimension; verification
  catches missing parents, not every cross-tenant combination.
- `last4`, `scac`, `currency` and similar fixed-width columns are generated and
  truncated/padded by the database, as they would be in a real load.
