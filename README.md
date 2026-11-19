# ClipForge

Turn long horizontal videos into short vertical clips with fully controllable,
word-by-word captions. Everything runs locally on your machine: no uploads to a
third-party service, no per-minute fees.

Built for creators who care about caption typography — you get five ready-made
looks plus complete control over fonts, colours, stroke, glow, animation and
position, and every export is a real burned-in MP4.

---

## What it does

1. **Ingest** — upload a video file or paste a YouTube link (via `yt-dlp`).
2. **Transcribe** — word-level timestamps with `faster-whisper` (fast, CPU-friendly).
3. **Find clips** — an LLM picks the 5–10 most engaging 20–60 s segments, each with
   a hook title and virality score. Without an API key it falls back to a built-in
   keyword/pace scorer, so the app works fully offline. Cuts always snap to whole
   sentences and never split a word.
4. **Reframe** — MediaPipe face detection tracks the speaker and drives a smoothed
   9:16 crop. Wide shots can use a blurred background instead.
5. **Caption** — ASS subtitles burned in through FFmpeg, with the active word
   highlighted and scaled, karaoke fill, typewriter reveal, or clean fades.
6. **Export** — 1080×1920 H.264 MP4 with AAC audio, optional music with auto-ducking
   and an optional logo overlay.

Everything is driven from a browser UI with a live phone-shaped preview, a
clickable timeline, a trim editor and an inline transcript fixer.

---

## Project structure

```
.
├── server/                     FastAPI backend
│   ├── main.py                 HTTP API + static host for the built UI
│   ├── config.py               Paths, limits, env fallbacks
│   ├── store.py                Settings / preset JSON persistence
│   ├── jobs.py                 Background job runner with progress
│   ├── library.py              Project folders on disk
│   └── pipeline/
│       ├── media.py            ffmpeg / ffprobe wrappers, progress parsing
│       ├── transcribe.py       faster-whisper + cached model
│       ├── text.py             Sentence spans, fillers, silences, emoji, chunking
│       ├── llm.py              LLM clip detection + offline heuristic fallback
│       ├── reframe.py          MediaPipe face tracking -> crop keyframes
│       ├── audio.py            Edit plans (keep ranges) and time remapping
│       ├── fonts.py            Bundled / uploaded / Google Fonts
│       ├── render.py           The FFmpeg graph: reframe + ASS + music + logo
│       ├── analyze.py          Phase 1 job: ingest -> transcript -> clips
│       ├── export.py           Phase 4 job: render one or many clips
│       └── captions/
│           ├── base.py         CaptionStyle model, renderers, ASS writer
│           └── presets.py      The style catalogue  ← add styles here
├── web/                        React + TypeScript UI (Vite)
│   └── src/
│       ├── components/         Home, Editor, Preview, StylePanel, …
│       ├── api.ts  types.ts  lib.ts  state.tsx
├── assets/fonts/               Bundled fonts (Archivo Black, Inter, Poppins, …)
├── assets/samples/             A small demo video for testing
├── tests/                      pytest suite (pipeline + caption writer)
├── data/                       Runtime: uploads, projects, exports, presets
├── requirements.txt
└── dev.sh                      Starts API + UI together
```

---

## Requirements

| Component | Version | Notes |
|---|---|---|
| Python | 3.11 – 3.12 | 3.12 recommended |
| Node + pnpm | Node 20+, pnpm 9+ | UI only |
| FFmpeg | 6.x or newer | must include `libass` (standard in distro builds) |
| RAM | 4 GB+ | 8 GB comfortable for `small` Whisper |

System packages (needed by MediaPipe and FFmpeg):

```bash
# Debian / Ubuntu
sudo apt-get update
sudo apt-get install -y ffmpeg libegl1 libgles2 libgl1 fonts-noto-color-emoji

# macOS
brew install ffmpeg
# (MediaPipe ships its own bundle on macOS)
```

> `libEGL`/`libGL` are easy to miss — without them face detection silently
> disables itself and clips fall back to a centre crop.

---

## Setup

```bash
git clone <your-repo> clipforge && cd clipforge

# 1. Python environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt     # or: uv pip install -r requirements.txt

# 2. Web dependencies
cd web && pnpm install && cd ..

# 3. Run both servers
./dev.sh
```

Open **http://localhost:3000**.

The first transcription downloads the Whisper model (~75 MB for `base`) to
`~/.cache/clipforge`. The face detector model (~230 KB) downloads on first analysis.

### Running the parts separately

```bash
# API only (port 8000)
PYTHONPATH=. .venv/bin/uvicorn server.main:app --reload --port 8000

# UI dev server (port 3000, proxies /api -> 8000)
cd web && pnpm dev
```

### Production build

```bash
cd web && pnpm build          # emits web/dist
cd .. && PYTHONPATH=. .venv/bin/uvicorn server.main:app --port 8000
```

Then open **http://localhost:8000** — FastAPI serves the built UI directly.

### Deploying with Docker

This is a Python service with ffmpeg, Whisper and MediaPipe, so it belongs on a
machine that can run them — not a static host.

```bash
docker compose up -d --build      # build + start on :8000
docker compose logs -f            # watch the first start
```

All state lives in the `clipforge-data` volume (`/data`): uploads, projects,
exports, downloaded Whisper models and an optional `cookies.txt`. Back that
volume up, and keep it across redeploys so users do not lose their work.

Put it behind a reverse proxy with TLS (Caddy or nginx). Encoding is CPU-bound:
give it at least 2 vCPU and 4 GB RAM, and prefer disk-backed SSDs for `/data`.
Run one container process (the default) — extra workers would each hold their
own job table.


---

## Configuration

Open **Settings** in the top bar. Values are stored in `data/settings.json`
(plain JSON, never committed).

| Setting | Default | Purpose |
|---|---|---|
| LLM API key | *(empty)* | Any OpenAI-compatible key. Empty = offline scoring |
| Model | `gpt-4o-mini` | Any chat model your provider serves |
| Base URL | `https://api.openai.com/v1` | Works with OpenRouter, Groq, local vLLM, Ollama |
| Whisper model | `base` | `tiny` / `base` / `small` / `medium` / `large-v3` |

Environment variables are used as defaults: `LLM_API_KEY` (or `OPENAI_API_KEY`),
`LLM_MODEL`, `LLM_BASE_URL`, `WHISPER_MODEL`.

Other environment knobs:

| Variable | Default | Meaning |
|---|---|---|
| `CLIPFORGE_DATA` | `./data` | Where projects, exports and presets live |
| `CLIPFORGE_MAX_UPLOAD` | `2147483648` | Upload cap in bytes (2 GB) |
| `PORT` | `3000` | UI dev port |

---

## Using it

1. Drop a video in, or paste a YouTube URL. Analysis starts immediately.
2. Wait for the progress bar — transcription is the slow part (~10 s per minute
   of speech on CPU with `base`).
3. Pick a clip on the left. The phone preview plays with live captions.
4. Style it in the right panel:
   - **Style** — animation, colours, outline, shadow, glow, box, pop scale/speed
   - **Type** — font, size, uppercase, letter spacing, auto-emoji
   - **Layout** — vertical position, words per line, max characters, margins
5. Trim with the sliders; cuts snap to whole words.
6. **Render frame** gives an exact FFmpeg still to confirm what the export will look like.
7. **Export clip** (or **Export all**) produces the MP4; download links appear under the clip list.

---

## Captions: how the engine works

The preview and the export share the same style data, so what you see is what
gets burned in.

`CaptionStyle` (in `server/pipeline/captions/base.py`) holds every knob as plain
data. `build_ass()` turns words + style into an ASS subtitle file; FFmpeg burns it
with `subtitles=…:fontsdir=…`. The browser preview applies the same values in CSS.

**Animation modes** are independent from colours and typography:

| Mode | Behaviour |
|---|---|
| `pop` | Whole line visible, active word highlighted and scaled up |
| `karaoke` | Line fills word by word using ASS `\kf` timing |
| `typewriter` | Words revealed progressively with a cursor |
| `fade` | Clean line with a short fade in/out |
| `none` | Static line |

### Adding a caption style

Styles are data — no code needed. Add an entry to `PRESETS` in
`server/pipeline/captions/presets.py`:

```python
"my_style": {
    "preset": "my_style", "animation": "pop", "font": "Bebas Neue",
    "font_size": 104, "uppercase": True,
    "primary_color": "#FFFFFF", "highlight_color": "#FF3D81",
    "outline_color": "#000000", "outline_width": 9,
    "position": "bottom", "offset": 0.74, "words_per_line": 2,
},
```

It appears in the UI immediately. If the font is not installed locally, ClipForge
downloads it from Google Fonts on first render so exports match the preview.

For behaviour that data cannot express, subclass `CaptionRenderer`, implement
`events_for_line()`, and register the class in `RENDERERS`. Built-in examples
are `PopRenderer`, `KaraokeRenderer` and `TypewriterRenderer`.

---

## Extra features

- **Silence & filler removal** — cuts pauses longer than 0.55 s and removes *um/uh*
  style fillers, then re-times the captions onto the edited timeline.
- **Background music** — upload a track; it is trimmed/looped to the clip and
  ducked under speech with `sidechaincompress`. Volume and fade are adjustable.
- **Watermark** — upload a PNG/JPG, choose corner and width.
- **Presets** — save any style under a name from the Style panel; it is stored in
  `data/presets.json` and appears in the style grid.
- **Batch export** — "Batch select", tick clips, then "Export all".
- **Custom fonts** — upload `.ttf`/`.otf`, or type a Google Fonts family name.

---

## API overview

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Engine status, presets, settings summary |
| `GET/PUT` | `/api/settings` | Read/update LLM + Whisper settings |
| `GET/POST` | `/api/presets` | List / save style presets |
| `GET` | `/api/fonts` | Available fonts |
| `POST` | `/api/fonts/upload` | Upload a `.ttf`/`.otf` |
| `POST` | `/api/fonts/google` | Download a Google Fonts family |
| `POST` | `/api/assets` | Upload background music or a watermark |
| `POST` | `/api/projects/upload` | Upload a video (multipart) |
| `POST` | `/api/projects/youtube` | Import from a URL |
| `GET` | `/api/projects` | List projects |
| `GET/PATCH/DELETE` | `/api/projects/{id}` | Read / update options / delete |
| `POST` | `/api/projects/{id}/reanalyze` | Re-transcribe and re-detect |
| `POST` | `/api/projects/{id}/detect` | Re-detect clips from cached transcript |
| `PATCH` | `/api/projects/{id}/clips/{cid}` | Trim, restyle, fix text |
| `POST` | `/api/projects/{id}/clips/{cid}/preview-frame` | Exact rendered still |
| `POST` | `/api/projects/{id}/clips/{cid}/export` | Render one clip |
| `POST` | `/api/projects/{id}/export` | Batch render |
| `GET` | `/api/jobs`, `/api/jobs/{id}` | Background job progress |

Uploads are capped at 2 GB and rejected if FFprobe cannot read a video stream.
Output filenames are slugified clip titles, so downloads are readable.

---

## Troubleshooting

**"Face tracking disabled" / clips are centre-cropped**
MediaPipe cannot load its graphics runtime. On Linux install `libegl1 libgles2 libgl1`.

**Captions render in the wrong font**
Check the font appears in the Type tab. If you typed a Google Fonts name, make sure
the download succeeded (the toast will say so). System fallback is DejaVu Sans.

**Export fails with a filter error**
Confirm `ffmpeg -filters | grep subtitles` lists the `subtitles` filter — it needs
`libass`. Distro builds include it; some static builds do not.

**Transcription is very slow**
Switch Whisper to `tiny` or `base` in Settings. CPU `int8` inference is used
automatically; GPU is not configured.

**YouTube import fails**
`yt-dlp` needs updating frequently: `pip install -U yt-dlp`. Some videos are
region- or age-restricted.

**Preview is black**
The browser needs byte-range support to seek the source video; ClipForge serves
it, so check that the API is reachable. Thumbnails appear even if the video codec
is unsupported by your browser (the canvas preview needs H.264).

---

## Tests

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q --junitxml=test-results/junit.xml
```

The suite covers the ASS writer (colours, positions, all presets), sentence and
clip boundary logic, filler/silence removal with time remapping, and clip scoring.
It needs no model downloads and no FFmpeg.

End-to-end checking with the bundled sample:

```bash
# transcribe + detect + render using the demo video
PYTHONPATH=. .venv/bin/python scripts/demo.py
```

---

## Notes and limits

- CPU-only. A 20-minute 1080p video takes roughly 3–5 minutes to analyse on a
  4-core machine, plus ~1 minute per exported clip.
- Silences and fillers are removed by cutting the audio/video stream, so the
  result jumps slightly at each cut. This is intentional and matches how
  OpusClip-style tools behave; extreme trimming can sound abrupt.
- Emoji rendering requires a colour emoji font (`fonts-noto-color-emoji`).
- YouTube downloading is your responsibility; only process videos you have rights to.

## Licence

MIT. Bundled fonts keep their original licences (SIL Open Font License).

## Pricing page

`/pricing.html` is a self-contained marketing page (Tailwind via CDN, no build step).
Open it from the home screen's **Pricing** button or directly at `/pricing.html`.

To make it yours:

- **Brand**: replace `ClipForge` in the `<title>`, the navbar, the footer and the OG tags.
- **Colour**: edit `accent` in the inline `tailwind.config` block (`#7C5CFF`).
- **Prices**: update the `PLANS` object near the bottom of the file — monthly, annual per-month and credits.
- **Links**: every `href="#"` is a placeholder (nav, socials, legal, footer columns).
- **Copy**: replace the three testimonial cards, which are labelled as placeholders on purpose.
- **Guarantee and FAQ**: check the refund window and credit definition match your real policy before launch.
- **Canonical / OG URLs**: swap `https://example.com` for your domain and add `og-pricing.png`.
