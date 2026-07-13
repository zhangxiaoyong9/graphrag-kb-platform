#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DB_PATH="${KB_DB_PATH:-kb.db}"
DATA_ROOT="${KB_DATA_ROOT:-.}"
HOST="${KB_HOST:-127.0.0.1}"
PORT="${KB_PORT:-8000}"
SKIP_INSTALL=0
SKIP_BUILD=0

usage() {
  cat <<'EOF'
Usage: ./scripts/start.sh [options]

Options:
  --db PATH          SQLite database path (default: kb.db)
  --data-root PATH   Index and vector data root (default: .)
  --host HOST        API bind host (default: 127.0.0.1)
  --port PORT        API bind port (default: 8000)
  --skip-install     Do not run uv sync or npm install
  --skip-build       Do not build web/dist when it is missing
  -h, --help         Show this help

The same values can be set with KB_DB_PATH, KB_DATA_ROOT, KB_HOST, and KB_PORT.
EOF
}

while (($#)); do
  case "$1" in
    --db) DB_PATH="$2"; shift 2 ;;
    --data-root) DATA_ROOT="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --skip-install) SKIP_INSTALL=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

command -v uv >/dev/null 2>&1 || { echo "Error: uv is not installed." >&2; exit 1; }

if ((SKIP_INSTALL == 0)) && [[ ! -d .venv ]]; then
  echo "[setup] Installing Python dependencies..."
  uv sync
fi

if [[ ! -d web/dist ]] && ((SKIP_BUILD == 0)); then
  command -v npm >/dev/null 2>&1 || { echo "Error: npm is required to build web/dist." >&2; exit 1; }
  echo "[setup] Building the dashboard..."
  if [[ -f web/package-lock.json ]]; then
    (cd web && { [[ -d node_modules ]] || npm ci; } && npm run build)
  else
    (cd web && { [[ -d node_modules ]] || npm install; } && npm run build)
  fi
fi

echo "[setup] Applying database migrations..."
uv run alembic upgrade head

SERVER_PID=""
WORKER_PID=""
cleanup() {
  trap - INT TERM EXIT
  echo
  echo "[stop] Stopping KB Platform..."
  [[ -n "$SERVER_PID" ]] && kill "$SERVER_PID" 2>/dev/null || true
  [[ -n "$WORKER_PID" ]] && kill "$WORKER_PID" 2>/dev/null || true
  [[ -n "$SERVER_PID" ]] && wait "$SERVER_PID" 2>/dev/null || true
  [[ -n "$WORKER_PID" ]] && wait "$WORKER_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "[start] API: http://${HOST}:${PORT}"
uv run python -m kb_platform.server "$DB_PATH" "$DATA_ROOT" "$HOST" "$PORT" &
SERVER_PID=$!

echo "[start] Worker"
uv run python -m kb_platform.worker "$DB_PATH" &
WORKER_PID=$!

# macOS still ships Bash 3.2, which has no `wait -n`.
while kill -0 "$SERVER_PID" 2>/dev/null && kill -0 "$WORKER_PID" 2>/dev/null; do
  sleep 1
done

STATUS=1
if ! kill -0 "$SERVER_PID" 2>/dev/null; then
  set +e
  wait "$SERVER_PID"
  STATUS=$?
  set -e
elif ! kill -0 "$WORKER_PID" 2>/dev/null; then
  set +e
  wait "$WORKER_PID"
  STATUS=$?
  set -e
fi
exit "$STATUS"
