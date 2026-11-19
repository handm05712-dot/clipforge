#!/usr/bin/env bash
# ClipForge dev runner: FastAPI on 8000, Vite dev server on $PORT (proxying /api).
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-3000}"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "!! ffmpeg is required. On Debian/Ubuntu: sudo apt-get install -y ffmpeg libegl1 libgles2"
fi

if [ ! -x .venv/bin/python ]; then
  echo "Creating Python virtualenv…"
  if command -v uv >/dev/null 2>&1; then
    uv venv -q .venv --python 3.12
  else
    python3 -m venv .venv
  fi
fi

if ! .venv/bin/python -c "import fastapi, faster_whisper, mediapipe, cv2, fontTools" >/dev/null 2>&1; then
  echo "Installing Python dependencies (first run takes a few minutes)…"
  if command -v uv >/dev/null 2>&1; then
    uv pip install -q --python .venv/bin/python -r requirements.txt
  else
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r requirements.txt
  fi
fi

if [ ! -d web/node_modules ]; then
  echo "Installing web dependencies…"
  (cd web && pnpm install)
fi

cleanup() {
  kill "${API_PID:-}" "${WEB_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

PYTHONPATH="." .venv/bin/uvicorn server.main:app --host 127.0.0.1 --port 8000 \
  --reload --reload-dir server &
API_PID=$!

cd web
pnpm exec vp dev --port "$PORT" --strictPort --host 0.0.0.0 &
WEB_PID=$!

wait -n "${API_PID}" "${WEB_PID}"
