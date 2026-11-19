"""Tiny JSON persistence helpers (jobs, presets, settings)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from . import config

_lock = threading.RLock()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def load_settings() -> dict:
    s = read_json(config.SETTINGS_FILE, {}) or {}
    return {
        "llm_base_url": s.get("llm_base_url", config.LLM_BASE_URL),
        "llm_model": s.get("llm_model", config.LLM_MODEL),
        "llm_api_key": s.get("llm_api_key", config.LLM_API_KEY),
        "whisper_model": s.get("whisper_model", config.WHISPER_MODEL),
        "language": s.get("language", "auto"),
    }


def save_settings(patch: dict) -> dict:
    current = load_settings()
    for key in ("llm_base_url", "llm_model", "llm_api_key", "whisper_model", "language"):
        if key in patch and patch[key] is not None:
            current[key] = patch[key]
    write_json(config.SETTINGS_FILE, current)
    return current


def public_settings() -> dict:
    s = load_settings()
    return {
        "llm_base_url": s["llm_base_url"],
        "llm_model": s["llm_model"],
        "llm_configured": bool(s["llm_api_key"]),
        "whisper_model": s["whisper_model"],
        "language": s["language"],
    }


BRAND_FILE = config.DATA / "brand.json"


def load_brand() -> dict:
    data = read_json(BRAND_FILE, {}) or {}
    return {
        "name": data.get("name") or "",
        "logo_path": data.get("logo_path"),
        "colors": data.get("colors") or [],
        "fonts": data.get("fonts") or [],
        "watermark": data.get("watermark") or {
            "position": "top-right", "width": 180, "opacity": 0.85,
        },
    }


def save_brand(patch: dict) -> dict:
    brand = load_brand()
    for key in ("name", "logo_path", "colors", "fonts", "watermark"):
        if key in patch:
            brand[key] = patch[key]
    write_json(BRAND_FILE, brand)
    return brand


DEFAULT_PRESETS: list[dict] = []


def load_presets() -> list[dict]:
    return read_json(config.PRESETS_FILE, []) or []


def save_presets(presets: list[dict]) -> None:
    write_json(config.PRESETS_FILE, presets)
