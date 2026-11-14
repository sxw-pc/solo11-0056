#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    SEP=";"
    SRC_PATH="$(cygpath -w "$ROOT/src")"
    ;;
  *)
    SEP=":"
    SRC_PATH="$ROOT/src"
    ;;
esac
export PYTHONPATH="$SRC_PATH${PYTHONPATH:+$SEP$PYTHONPATH}"

PORT="${PORT:-5188}"
mkdir -p build

echo "[1/4] pytest"
python -m pytest -q

echo "[2/4] run pipeline"
python -m schema_extractor.cli run \
  --config configs/default.yaml \
  --now 2026-01-01T00:00:00Z

echo "[3/4] serve smoke on 127.0.0.1:${PORT}"
python -m schema_extractor.cli serve \
  --config configs/default.yaml \
  --port "$PORT" >build/serve.log 2>&1 &
SERVER_PID=$!
cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT

READY=0
for _ in $(seq 1 50); do
  if curl -fsS "http://127.0.0.1:${PORT}/" 2>/dev/null | grep -q "标程抽取台"; then
    READY=1
    break
  fi
  sleep 0.2
done
if [ "$READY" -ne 1 ]; then
  echo "serve smoke failed; see build/serve.log" >&2
  exit 1
fi

echo "[4/4] stop server"
cleanup
trap - EXIT
echo "all checks passed"
