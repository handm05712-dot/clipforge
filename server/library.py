"""Project storage: one folder per video under data/projects/<id>/."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import config, jobs, store
from .pipeline import media


def projects_root() -> Path:
    d = config.DATA / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_dir(pid: str) -> Path:
    return projects_root() / pid


def project_file(pid: str) -> Path:
    return project_dir(pid) / "project.json"


def new_project(title: str, source: str, video: Path, media_info: dict) -> dict:
    pid = jobs.new_id("p")
    d = project_dir(pid)
    d.mkdir(parents=True, exist_ok=True)
    project = {
        "id": pid,
        "title": title,
        "source": source,
        "video_path": str(Path(video).resolve()),
        "created": time.time(),
        "media": media_info,
        "transcript": None,
        "clips": [],
        "options": default_options(),
        "style": None,
        "status": "uploaded",
        "poster": None,
    }
    save(project)
    return project


def save(project: dict) -> None:
    store.write_json(project_file(project["id"]), project)


def load(pid: str) -> dict | None:
    data = store.read_json(project_file(pid))
    if not data:
        return None
    if repair_clip_words(data):
        save(data)
    if not Path(data["video_path"]).exists():
        return data
    return data


def repair_clip_words(project: dict) -> bool:
    """Fill in captions for clips saved without them.

    Manually added clips used to be stored with an empty `words` list, so the
    editor and the renderer had nothing to caption. The transcript still has
    the timings, so rebuild them. Returns True when something changed.
    """
    from .pipeline import text as txt

    transcript = project.get("transcript") or {}
    words = transcript.get("words") or []
    if not words:
        return False
    changed = False
    for clip in project.get("clips") or []:
        if clip.get("words_override") or clip.get("words"):
            continue
        found = txt.words_between(words, float(clip.get("start") or 0), float(clip.get("end") or 0))
        if found:
            clip["words"] = found
            changed = True
    return changed


def require(pid: str) -> dict:
    project = load(pid)
    if not project:
        raise KeyError(f"Project {pid} not found")
    return project


def list_projects() -> list[dict]:
    out = []
    for f in projects_root().glob("*/project.json"):
        data = store.read_json(f)
        if not data:
            continue
        out.append({
            "id": data["id"], "title": data.get("title", "Untitled"),
            "created": data.get("created"), "duration": (data.get("media") or {}).get("duration"),
            "width": (data.get("media") or {}).get("width"),
            "height": (data.get("media") or {}).get("height"),
            "clip_count": len(data.get("clips") or []),
            "status": data.get("status"),
            "poster": data.get("poster"),
            "source": data.get("source"),
        })
    return sorted(out, key=lambda p: p.get("created") or 0, reverse=True)


def delete(pid: str) -> None:
    d = project_dir(pid)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    exports = config.EXPORTS / pid
    if exports.exists():
        shutil.rmtree(exports, ignore_errors=True)


def exports_dir(pid: str) -> Path:
    d = config.EXPORTS / pid
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_options() -> dict:
    return {
        "reframe": "face",
        "blur_background": False,
        "remove_silences": True,
        "remove_fillers": True,
        "voice_boost": True,
        "music_path": None,
        "music_volume": 0.18,
        "music_fade": 1.0,
        "auto_duck": True,
        "watermark_path": None,
        "watermark_width": 180,
        "watermark_opacity": 0.85,
        "watermark_position": "top-right",
        "clip_min": 20,
        "clip_max": 60,
        "clip_count": 7,
        "provider": "auto",
        # output target
        "platform": "reels",
        "aspect": "9:16",
        "quality": "high",
        # gameplay layout
        "layout": "standard",     # standard | gameplay_top | gameplay_full
        "gameplay_path": None,
        "sfx": [],
    }
