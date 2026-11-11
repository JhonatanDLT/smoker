#!/usr/bin/env bash
# Load the demo schema, generate data, verify it and open the report.
set -euo pipefail

DB="${1:-smoker_demo}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT"

psql -d "$DB" -q -f examples/saas-billing/schema.sql

export SMOKER_SOURCE_DSN="${SMOKER_SOURCE_DSN:-postgresql:///$DB}"
export SMOKER_TARGET_DSN="${SMOKER_TARGET_DSN:-postgresql:///$DB}"

.venv/bin/smoker run \
  --rows "${ROWS:-200}" \
  --seed 42 \
  --spec examples/saas-billing/plan.yml \
  --out proof

echo
echo "Report: $ROOT/proof/report.html"
