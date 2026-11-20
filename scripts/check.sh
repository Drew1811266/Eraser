#!/usr/bin/env bash
# The M1 quality gate. Everything here must pass before a milestone is called done.
#
#   ./scripts/check.sh
#
# Requires: python 3.11+, node 22.6+ (native TypeScript stripping).
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python}"

echo "== [1/3] contract artifacts are in sync =="
"$PY" contract/codegen/generate.py --check

echo
echo "== [2/3] engine unit tests =="
"$PY" -m unittest discover -s tests -t . -q

echo
echo "== [3/3] engine <-> shell end-to-end =="
node scripts/e2e-engine.mjs

echo
echo "ALL GATES PASSED"
