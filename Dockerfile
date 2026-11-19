# ClipForge — production image.
#
# The app is a Python service, not a static site: it shells out to ffmpeg,
# runs Whisper on CPU and tracks faces with MediaPipe. So it ships as a
# container with the system media tools baked in, and the UI is built once
# into web/dist where FastAPI serves it at /.

# ---------------------------------------------------------------- UI build ----
FROM node:22-slim AS web

RUN corepack enable && corepack prepare pnpm@12.21.0 --activate
WORKDIR /app/web

# Install from the lockfile first so a source edit does not re-resolve deps.
# pnpm-workspace.yaml holds the `catalog:` versions, so it must be copied too.
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY web/ ./
RUN pnpm run build

# --------------------------------------------------------------- runtime -----
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    CLIPFORGE_DATA=/data \
    WHISPER_CACHE=/data/models/whisper \
    PORT=8000

# ffmpeg/ffprobe for cutting and encoding; the GL libraries are what MediaPipe
# loads even in headless mode; the fonts cover caption defaults and emoji.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libegl1 libgles2 libgl1 libglib2.0-0 \
        fonts-dejavu-core fonts-noto-color-emoji \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/
COPY assets/ ./assets/
COPY scripts/ ./scripts/
COPY --from=web /app/web/dist ./web/dist

# Media, exports and cached models live on a volume so a redeploy does not
# throw away the user's projects or make them re-download the Whisper model.
RUN mkdir -p /data && useradd --create-home --uid 10001 clipforge \
    && chown -R clipforge:clipforge /data /app
USER clipforge

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

# One worker on purpose: jobs run on threads inside the process and share the
# job registry and project files. Scale by giving the container more CPU.
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
