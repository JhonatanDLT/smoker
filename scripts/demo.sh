#!/usr/bin/env bash
# Load the demo schema, generate data, verify it and open the report.
set -euo pipefail

DB="${1:-pgproof_demo}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT"

psql -d "$DB" -q -f examples/saas-billing/schema.sql

export PGPROOF_SOURCE_DSN="${PGPROOF_SOURCE_DSN:-postgresql:///$DB}"
export PGPROOF_TARGET_DSN="${PGPROOF_TARGET_DSN:-postgresql:///$DB}"

.venv/bin/pgproof run \
  --rows "${ROWS:-200}" \
  --seed 42 \
  --spec examples/saas-billing/plan.yml \
  --out proof

echo
echo "Report: $ROOT/proof/report.html"
