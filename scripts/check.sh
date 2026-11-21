#!/usr/bin/env bash
# The quality gate. Everything here must pass before a milestone is called done.
#
#   ./scripts/check.sh          # fast gates (~15 s): contract, units, protocol, UI units
#   ./scripts/check.sh --full   # + real video, real upscale, real browser UI (~2 min)
#
# Requires: python 3.11+, node 22.6+ (native TypeScript stripping), and for
# --full a built desktop bundle plus Playwright's chromium
# (`npm --prefix desktop run build && npx --prefix desktop playwright install chromium`).
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python}"
FULL=0
for arg in "$@"; do
  case "$arg" in
    --full) FULL=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# Prefer the project venv: the engine needs numpy/opencv/onnxruntime, which the
# system interpreter does not have.
if [ -z "${PYTHON:-}" ] && [ -x ".venv/Scripts/python.exe" ]; then
  PY=".venv/Scripts/python.exe"
elif [ -z "${PYTHON:-}" ] && [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
fi
export ERASER_PYTHON="$PY"

STEPS=4
if [ "$FULL" = "1" ]; then
  STEPS=7
fi

echo "== [1/$STEPS] contract artifacts are in sync =="
"$PY" contract/codegen/generate.py --check

echo
echo "== [2/$STEPS] engine unit tests =="
"$PY" -m unittest discover -s tests -t . -q

echo
echo "== [3/$STEPS] engine <-> shell end-to-end =="
node scripts/e2e-engine.mjs

echo
echo "== [4/$STEPS] shell typecheck + UI tests =="
npm --prefix desktop run check

if [ "$FULL" = "1" ]; then
  echo
  echo "== [5/$STEPS] real video through the pipeline =="
  "$PY" scripts/e2e-video.py

  echo
  echo "== [6/$STEPS] Real-ESRGAN upscale =="
  "$PY" scripts/e2e-upscale.py

  echo
  echo "== [7/$STEPS] built UI in a real browser, against the real engine =="
  node scripts/e2e-renderer.mjs
fi

echo
echo "ALL GATES PASSED"
