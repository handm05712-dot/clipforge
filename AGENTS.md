# Project notes

freecode reads this file before every change. Add anything it should always remember about this project.

## ClipForge — long video → captioned vertical clips

### Architecture decisions
- **Python 3.12 + FastAPI** backend in `server/`, React 19 + TS frontend in `web/` (pnpm, Vite+).
- `dev.sh` runs uvicorn on **8000** and Vite on **$PORT (3000)**; Vite proxies `/api`.
  In production FastAPI serves `web/dist` at `/`.
- **Captions are ASS subtitles** burned by FFmpeg (`subtitles=...:fontsdir=...`), not Remotion.
  `server/pipeline/captions/base.py` holds `CaptionStyle` (plain data) + renderer classes;
  `presets.py` holds the style catalogue. Adding a style = adding a dict, no code.
- **Time convention (important):** stored clip `words` are in *absolute* source-video time.
  Everything inside `render.render_clip` works in *clip-local* seconds. Convert with
  `w.start - clip.start` before building edit plans or captions. This caused a real bug once
  (clips not starting at 0 rendered 2s long) — see the regression tests.
- Reframing uses MediaPipe Tasks face detector (model auto-downloaded to `data/models`).
  The crop path is smoothed keyframes applied via FFmpeg `sendcmd` on a named `crop@cf`.
- Silences/fillers are removed by cut+concat via `select`/`aselect` filters, then captions are
  remapped onto the compressed timeline.

### Environment gotchas
- MediaPipe needs system `libegl1 libgles2 libgl1` or it silently disables face detection.
- Pin `av<13`: faster-whisper 1.2 calls `av.open(metadata_errors=...)` which PyAV 13 dropped.
- Fonts dir for libass is `data/fontdir`, populated with **symlinks** (not copies) to keep the
  workspace small. Don't switch back to copying.
- Playback: the canvas redraws only when the frame or style changes, React ticks at 30fps,
  and /api/jobs polls 1.2s only while a job runs (6s idle). Keep it that way; a fixed
  60fps loop plus a 1.2s poll made the editor feel sluggish.
- Exports default to `quality: high` (crf 16) and small sources get a light `unsharp` pass
  after upscaling. A 480p source now exports near 9 Mbps instead of ~96 kbps.
- The browser only shows a bundled font if its `@font-face` exists. `ensureFontFaces()` in
  `web/src/lib.ts` injects them all at startup; without it every picker row fell back to the
  system serif and the preview looked like the font was missing.
- `data/` is runtime only and can be deleted; keep the workspace under the storage budget.

### Testing
- `PYTHONPATH=. .venv/bin/python -m pytest tests -q --junitxml=test-results/junit.xml` (no models/FFmpeg needed).
- `PYTHONPATH=. .venv/bin/python scripts/demo.py` runs the full real pipeline on the sample video.
- Typecheck: `cd web && pnpm run typecheck`. Build: `pnpm run build`.

### Marketing site
- `web/public/pricing.html` is a standalone, framework-free Tailwind-CDN page served at
  `/pricing.html` in dev and copied into `web/dist` on build. Keep it a single file.
- Brand tokens live in the `tailwind.config` inline block (`ink`, `panel`, `accent`).
  The page is the free/MIT model: there are **no** prices, plan tiers, credits or
  billing toggle. Don't reintroduce them without the user asking.
- The only numbers are the cloud-cost estimator (`#srcMin`, `#inPrice`, `#outPrice`)
  using 1100 input / 220 output tokens per source minute. It is labelled an
  estimate; the provider's invoice is authoritative.
- Compare table uses `border-collapse: separate` on purpose: `collapse` silently
  disables `position: sticky`, which the mobile first column depends on.

## Deployment (Docker)
- The app is a **Linux service**, not static: ffmpeg, faster-whisper (CPU) and MediaPipe.
  Static or Worker-only hosts cannot run it.
- `Dockerfile` is multi-stage: node builds `web/dist`, python:3.12-slim runs `uvicorn server.main:app`.
- All state lives in `/data` (`CLIPFORGE_DATA`): uploads, projects, exports,
  `models/whisper` (`WHISPER_CACHE`) and an optional `cookies.txt`. Mount it as a
  volume — losing it loses every project.
- Run one uvicorn worker: jobs are in-process threads sharing the job registry.
- `docker-compose.yml` maps 8000 and the `clipforge-data` volume. Put TLS in front.
