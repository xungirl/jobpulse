#!/usr/bin/env bash
# Local equivalent of .github/workflows/ci.yml. Usage: scripts/check.sh [pytest-repeats]
set -euo pipefail
cd "$(dirname "$0")/.."
export JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1
REPEATS="${1:-1}"

uv sync --frozen --group infra -q
uv run ruff check src tests infra
uv run ruff format --check src tests infra
uv run mypy
for i in $(seq 1 "$REPEATS"); do
  echo "--- pytest run $i/$REPEATS"
  uv run pytest -q -p no:cacheprovider | tail -1
done
(cd dashboard && npm ci --silent && npm run build --silent && git diff --exit-code -- ../src/jobpulse/web/app.js)
cdk synth -c alert_email=ci@example.com --quiet
echo "all checks passed"
