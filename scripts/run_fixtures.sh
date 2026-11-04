#!/usr/bin/env bash
# Load every fixture into its own database, generate, verify, write reports.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ROWS="${ROWS:-100}"

for fixture in ecommerce multi-tenant-crm analytics partitioned-events; do
  db="pgproof_${fixture//-/_}"
  echo "=== $fixture ($db) ==="
  dropdb --if-exists "$db" >/dev/null 2>&1 || true
  createdb "$db"
  psql -d "$db" -q -f "examples/$fixture/schema.sql"

  PGPROOF_SOURCE_DSN="postgresql:///$db" \
  PGPROOF_TARGET_DSN="postgresql:///$db" \
    .venv/bin/pgproof run \
      --rows "$ROWS" \
      --seed 42 \
      --spec "examples/$fixture/plan.yml" \
      --out "proof/$fixture"
done

echo
echo "Reports: proof/ecommerce, proof/multi-tenant-crm, proof/analytics"
