#!/usr/bin/env bash
# Provision and run every realistic schema against PostgreSQL and SQL Server.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ROWS="${ROWS:-500}"
SEED="${SEED:-42}"
PG_URL_PREFIX="${SMOKER_PG_URL_PREFIX:-postgresql:///}"
CONTAINER="${SMOKER_MSSQL_CONTAINER:-smoker-mssql}"
PASSWORD="${SMOKER_MSSQL_PASSWORD:-Smoker!2026Strong}"
SERVER="${SMOKER_MSSQL_SERVER:-127.0.0.1,14333}"
SQLCMD=(docker exec "$CONTAINER" /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$PASSWORD" -C)

if [[ ! -x .venv/bin/smoker ]]; then
  echo "missing .venv/bin/smoker; run: pip install -e '.[dev,sqlserver]'" >&2
  exit 1
fi

export DYLD_LIBRARY_PATH="${DYLD_LIBRARY_PATH:-/opt/homebrew/opt/openssl@3/lib}"

domains=(saas-billing ecommerce fintech logistics multi-tenant-crm healthcare)
schemas=(billing shop fintech logistics crm health)

has_mssql=0
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  has_mssql=1
else
  echo "warn: SQL Server container $CONTAINER not found; skipping SQL Server"
fi

for index in "${!domains[@]}"; do
  domain="${domains[$index]}"
  schema="${schemas[$index]}"
  db="smoker_real_${domain//-/_}"
  pg_dsn="${PG_URL_PREFIX}${db}"

  echo
  echo "=== $domain / postgres ==="
  dropdb --if-exists "$db" >/dev/null 2>&1 || true
  createdb "$db"
  psql -q -v ON_ERROR_STOP=1 -d "$db" \
    -f "examples/realistic/$domain/postgres/schema.sql"
  .venv/bin/smoker run \
    --dialect postgres \
    --source "$pg_dsn" --target "$pg_dsn" \
    --schema "$schema" --rows "$ROWS" --seed "$SEED" \
    --format json,html,junit \
    --out "proof/realistic/$domain/postgres"

  if [[ "$has_mssql" -eq 1 ]]; then
    echo
    echo "=== $domain / sqlserver ==="
    "${SQLCMD[@]}" -Q "IF DB_ID('$db') IS NOT NULL BEGIN ALTER DATABASE [$db] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [$db]; END; CREATE DATABASE [$db];" >/dev/null
    docker exec -i "$CONTAINER" /opt/mssql-tools18/bin/sqlcmd \
      -S localhost -U sa -P "$PASSWORD" -C -b -d "$db" \
      < "examples/realistic/$domain/sqlserver/schema.sql" >/dev/null
    dsn="DRIVER={ODBC Driver 18 for SQL Server};SERVER=$SERVER;DATABASE=$db;UID=sa;PWD=$PASSWORD;TrustServerCertificate=yes;Encrypt=yes"
    .venv/bin/smoker run \
      --dialect sqlserver \
      --source "$dsn" --target "$dsn" \
      --schema "$schema" --rows "$ROWS" --seed "$SEED" \
      --temporal-history \
      --format json,html,junit \
      --out "proof/realistic/$domain/sqlserver"
  fi
done

echo
echo "done: reports under $ROOT/proof/realistic"
