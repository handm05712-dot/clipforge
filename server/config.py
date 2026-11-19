"""Paths and runtime configuration for ClipForge."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CLIPFORGE_DATA", ROOT / "data")).resolve()

UPLOADS = DATA / "uploads"
JOBS = DATA / "jobs"
EXPORTS = DATA / "exports"
FONTS = DATA / "fonts"
MODELS = DATA / "models"
PRESETS_FILE = DATA / "presets.json"
SETTINGS_FILE = DATA / "settings.json"

BUNDLED_FONTS = ROOT / "assets" / "fonts"
SAMPLE_DIR = ROOT / "assets" / "samples"

FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)
FACE_MODEL = MODELS / "blaze_face_short_range.tflite"

MAX_UPLOAD_BYTES = int(os.environ.get("CLIPFORGE_MAX_UPLOAD", 2 * 1024**3))
TARGET_W, TARGET_H = 1080, 1920

# env fallbacks; the UI settings file overrides these
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
LLM_API_KEY = os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")


def ensure_dirs() -> None:
    for d in (DATA, UPLOADS, JOBS, EXPORTS, FONTS, MODELS):
        d.mkdir(parents=True, exist_ok=True)


ensure_dirs()
