#!/usr/bin/env bash
# Run the same checks as .github/workflows/ci.yml, locally.
#
#   ./scripts/ci_local.sh              # everything
#   ./scripts/ci_local.sh --backend    # python only
#   ./scripts/ci_local.sh --frontend   # web only
#
# Exits non-zero on the first failure.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

# Prefer the project venv when it exists (Windows: Scripts/, POSIX: bin/).
if [ -x ".venv/Scripts/python.exe" ]; then
  PY="$ROOT/.venv/Scripts/python.exe"
elif [ -x ".venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="$(command -v python3 || command -v python)"
fi

RUN_BACKEND=1
RUN_FRONTEND=1
case "${1:-}" in
  --backend)  RUN_FRONTEND=0 ;;
  --frontend) RUN_BACKEND=0 ;;
  "") ;;
  *) echo "unknown option: $1 (use --backend / --frontend)"; exit 2 ;;
esac

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

if [ "$RUN_BACKEND" = 1 ]; then
  step "Python test suite"
  PYTHONPATH="$ROOT" "$PY" -m pytest tests -q

  step "Zero-key demo (no API key, no network)"
  "$PY" -m kairos demo --json --quiet > /dev/null
  echo "demo: ok"

  step "i18n merge (strict)"
  "$PY" scripts/merge_i18n.py --strict

  step "i18n checker (hardcoded / missing strings)"
  node scripts/check_i18n.mjs
fi

if [ "$RUN_FRONTEND" = 1 ]; then
  step "Frontend typecheck"
  (cd web && npx tsc --noEmit)

  step "Frontend unit tests"
  (cd web && npx vitest run)
fi

printf '\n\033[1;32mAll checks passed.\033[0m\n'
