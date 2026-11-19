"""Export phase: render one or many clips, with progress and a batch job."""
from __future__ import annotations

import os
import time
from pathlib import Path

from .. import config, jobs, library
from . import media, render


def _slug(text: str, fallback: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else "" for c in (text or "")).strip()
    keep = "-".join(keep.split())[:60].lower()
    return keep or fallback


def export_one(project_id: str, clip_id: str, job_id: str) -> dict:
    project = library.require(project_id)
    clip = next((c for c in project.get("clips", []) if c["id"] == clip_id), None)
    if not clip:
        raise KeyError(f"Clip {clip_id} not found")
    out_dir = library.exports_dir(project_id)
    current = (clip.get("export") or {}).get("file")
    base = _slug(clip.get("title"), clip_id)
    if current:
        name = current
    else:
        taken = {c.get("export", {}).get("file") for c in project["clips"] if c.get("export")}
        name = f"{base or clip_id}.mp4"
        n = 2
        while name in taken:
            name = f"{base or clip_id}-{n}.mp4"
            n += 1
    out = out_dir / name
    workdir = config.JOBS / job_id / "work"

    def prog(p: float) -> None:
        jobs.update(job_id, 0.02 + p * 0.96, "Rendering", f"Encoding {name}…")

    # Render to a private temp file and swap it in at the end. Writing straight to
    # `out` let two concurrent exports of the same clip truncate each other
    # (the UI showed a 48-byte "video" because of exactly this).
    staged = workdir / f"out-{job_id}.mp4"
    result = render.render_clip(project, clip, staged, workdir, on_progress=prog)
    if not staged.exists() or staged.stat().st_size < 1024:
        raise RuntimeError("Render produced no video data. Check the source file and export settings.")
    out_dir.mkdir(parents=True, exist_ok=True)
    os.replace(staged, out)
    jobs.update(job_id, 0.99, "Finalizing", "Writing export…")
    clip["export"] = {
        "file": name,
        "url": f"/api/projects/{project_id}/exports/{name}",
        "duration": result["duration"],
        "created": time.time(),
        "size": out.stat().st_size if out.exists() else 0,
    }
    clip["keep_ranges"] = result["keep_ranges"]
    library.save(project)
    jobs.patch(job_id, files=[{"name": name, "url": clip["export"]["url"],
                               "clip_id": clip_id, "size": clip["export"]["size"]}])
    return result


def export_many(project_id: str, clip_ids: list[str], job_id: str) -> None:
    project = library.require(project_id)
    total = max(1, len(clip_ids))
    files = []
    for i, cid in enumerate(clip_ids):
        clip = next((c for c in project.get("clips", []) if c["id"] == cid), None)
        if not clip:
            continue
        jobs.update(job_id, i / total, "Batch export", f"Clip {i + 1} of {total}…")
        sub = f"{job_id}-{cid}"
        try:
            result = export_one(project_id, cid, sub)
            fresh = library.require(project_id)
            done = next((c for c in fresh["clips"] if c["id"] == cid), None)
            export = (done or {}).get("export") or {}
            files.append({
                "clip_id": cid,
                "url": export.get("url") or f"/api/projects/{project_id}/exports/{Path(result['path']).name}",
                "name": export.get("file") or Path(result["path"]).name,
                "size": Path(result["path"]).stat().st_size if Path(result["path"]).exists() else 0,
            })
        except Exception as exc:
            files.append({"clip_id": cid, "error": str(exc)[:300]})
        project = library.require(project_id)
    jobs.patch(job_id, files=files)
    jobs.update(job_id, 1.0, "Done", f"Exported {len([f for f in files if 'error' not in f])} clips")


def make_poster(project_id: str) -> str | None:
    project = library.require(project_id)
    src = Path(project["video_path"])
    duration = (project.get("media") or {}).get("duration") or 1
    out = library.project_dir(project_id) / "poster.jpg"
    try:
        media.thumbnail(src, min(3.0, duration / 3), out, width=420)
        project["poster"] = f"/api/projects/{project_id}/files/poster.jpg"
        library.save(project)
        return project["poster"]
    except Exception:
        return None
