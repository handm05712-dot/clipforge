"""ClipForge API."""
from __future__ import annotations

import mimetypes
import shutil
import time
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, history, jobs, library, store
from .pipeline import analyze as analyzer
from .pipeline import export as exporter
from .pipeline import fonts, media, platforms, social
from .pipeline.captions.base import CaptionStyle, build_ass
from .pipeline.captions.presets import PRESETS, list_presets, style_for
from .pipeline.render import render_preview_frame

app = FastAPI(title="ClipForge", version="1.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


# ---------------------------------------------------------------- health ----
@app.get("/api/health")
def health():
    return {
        "ok": True,
        "ffmpeg": media.ffmpeg_version(),
        "ytdlp": analyzer.ytdlp_available(),
        "cookies": (config.DATA / "cookies.txt").exists(),
        "face_model": config.FACE_MODEL.exists(),
        "presets": list(PRESETS.keys()),
        "platforms": platforms.list_presets(),
        "settings": store.public_settings(),
    }


# -------------------------------------------------------------- settings ----
@app.get("/api/settings")
def get_settings():
    return store.public_settings()


@app.put("/api/settings")
def put_settings(payload: dict = Body(...)):
    store.save_settings(payload)
    return store.public_settings()


# ---------------------------------------------------------------- assets ----
@app.post("/api/assets")
async def upload_asset(kind: str = Form("music"), file: UploadFile = File(...)):
    """Upload background music or a watermark image."""
    if kind not in ("music", "watermark"):
        raise HTTPException(400, "kind must be music or watermark")
    suffix = Path(file.filename or "").suffix.lower()
    allowed = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"} if kind == "music" \
        else {".png", ".jpg", ".jpeg", ".webp"}
    if suffix not in allowed:
        raise HTTPException(400, f"Unsupported file type {suffix or '(none)'}")
    assets_dir = config.DATA / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    out = assets_dir / f"{jobs.new_id(kind[:2])}-{Path(file.filename or 'asset').stem[:40]}{suffix}"
    data = await file.read()
    if len(data) > 120 * 1024 * 1024:
        raise HTTPException(413, "File too large")
    out.write_bytes(data)
    return {"kind": kind, "path": str(out), "name": file.filename, "size": len(data)}


# --------------------------------------------------------------- presets ----
@app.get("/api/presets")
def get_presets():
    return {"builtin": list_presets(), "saved": store.load_presets()}


@app.post("/api/presets")
def save_preset(payload: dict = Body(...)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Preset needs a name")
    style = CaptionStyle.from_dict(payload.get("style")).to_dict()
    presets = store.load_presets()
    existing = next((p for p in presets if p["name"].lower() == name.lower()), None)
    if existing:
        existing["style"] = style
        existing["updated"] = time.time()
    else:
        presets.append({"id": jobs.new_id("s"), "name": name, "style": style,
                        "created": time.time()})
    store.save_presets(presets)
    return {"saved": presets}


@app.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: str):
    presets = [p for p in store.load_presets() if p["id"] != preset_id]
    store.save_presets(presets)
    return {"saved": presets}


# ----------------------------------------------------------------- fonts ----
@app.get("/api/fonts")
def get_fonts():
    return {"fonts": fonts.list_fonts()}


@app.get("/api/fonts/file/{name}")
def font_file(name: str):
    """Serve a font file so the browser preview matches the render."""
    for f in fonts.list_fonts():
        if f["file"] == name and f["path"]:
            path = Path(f["path"])
            if path.exists():
                return FileResponse(path, media_type="font/ttf",
                                    headers={"Cache-Control": "max-age=86400"})
    raise HTTPException(404, "Font not found")


@app.post("/api/fonts/upload")
async def upload_font(file: UploadFile = File(...)):
    data = await file.read()
    try:
        info = fonts.save_upload(file.filename or "font.ttf", data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    fonts.ensure_installed()
    return info


@app.post("/api/fonts/google")
def google_font(payload: dict = Body(...)):
    family = (payload.get("family") or "").strip()
    try:
        info = fonts.download_google_font(family)
    except Exception as exc:
        raise HTTPException(400, f"Could not fetch '{family}': {exc}")
    fonts.ensure_installed()
    return info


@app.delete("/api/fonts/{filename}")
def remove_font(filename: str):
    return {"deleted": fonts.delete_font(filename)}


# -------------------------------------------------------------- projects ----
def _create_project(title: str, source: str, video: Path) -> tuple[dict, str]:
    try:
        info = media.probe(video)
    except media.MediaError as exc:
        raise HTTPException(400, f"Unreadable video: {exc}")
    project = library.new_project(title, source, video, info)
    job = jobs.create("analyze", title=project["title"], project_id=project["id"])
    jobs.start(job["id"], lambda jid: analyzer.analyze(project["id"], jid))
    exporter.make_poster(project["id"])
    return project, job["id"]


ALLOWED_VIDEO = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mts", ".m2ts"}


@app.post("/api/projects/upload")
async def upload_video(file: UploadFile = File(...), title: str = Form("")):
    suffix = Path(file.filename or "video.mp4").suffix.lower() or ".mp4"
    if suffix not in ALLOWED_VIDEO:
        raise HTTPException(
            400,
            f"{suffix} is not a supported video format. Use MP4, MOV, MKV, WEBM, M4V or AVI.",
        )
    # trust the declared size to reject huge files before writing anything
    declared = getattr(file, "size", None)
    if declared and declared > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            413,
            f"File is {declared / 1024**3:.1f} GB. The limit is "
            f"{config.MAX_UPLOAD_BYTES / 1024**3:.0f} GB.",
        )
    tmp = config.UPLOADS / f"upload-{jobs.new_id()}{suffix}"
    size = 0
    try:
        with open(tmp, "wb") as out:
            while chunk := await file.read(1024 * 1024 * 4):
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        413,
                        f"File is larger than "
                        f"{config.MAX_UPLOAD_BYTES / 1024**3:.0f} GB.",
                    )
                out.write(chunk)
    except HTTPException:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, f"Upload interrupted: {exc}") from exc
    if size == 0:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, "The uploaded file was empty.")
    try:
        project, job_id = _create_project(
            title or Path(file.filename or "Video").stem, "upload", tmp
        )
    except HTTPException:
        tmp.unlink(missing_ok=True)
        raise
    return {"project_id": project["id"], "job_id": job_id}


@app.post("/api/projects/youtube")
def youtube_project(payload: dict = Body(...)):
    url = (payload.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Paste a valid video URL")
    job = jobs.create("download", title="YouTube import")
    holder: dict = {}

    def work(jid: str) -> None:
        path = analyzer.ingest_youtube(url, jid)
        project, sub_id = _create_project(payload.get("title") or path.stem, url, path)
        holder.update({"project_id": project["id"], "job_id": sub_id})
        jobs.update(jid, 0.98, "Starting analysis", "Download complete. Analysing…")
        jobs.patch(jid, project_id=project["id"], analysis_job=sub_id)

    jobs.start(job["id"], work)
    return {"job_id": job["id"]}


@app.get("/api/projects")
def list_all():
    return {"projects": library.list_projects()}


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    project = library.load(pid)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    library.delete(pid)
    return {"ok": True}


VALIDATED_OPTIONS = {
    "music_volume", "music_fade", "watermark_width", "watermark_opacity",
    "clip_min", "clip_max", "clip_count",
}


def _check_options(patch: dict) -> None:
    for key in ("clip_min", "clip_max"):
        if key in patch:
            try:
                float(patch[key])
            except (TypeError, ValueError):
                raise HTTPException(400, f"{key} must be a number")
    if "clip_min" in patch and "clip_max" in patch and float(patch["clip_min"]) > float(patch["clip_max"]):
        raise HTTPException(400, "Minimum clip length cannot be greater than the maximum.")
    if "clip_count" in patch:
        n = int(patch["clip_count"])
        if not 1 <= n <= 20:
            raise HTTPException(400, "Clip count must be between 1 and 20.")
    for key in ("music_volume", "music_fade", "watermark_opacity"):
        if key in patch and patch[key] is not None:
            v = float(patch[key])
            if not 0 <= v <= 2:
                raise HTTPException(400, f"{key} must be between 0 and 2.")
    if "platform" in patch and patch["platform"] not in platforms.PLATFORMS:
        raise HTTPException(400, f"Unknown platform preset: {patch['platform']}")
    if "aspect" in patch and patch["aspect"] not in platforms.ASPECTS:
        raise HTTPException(400, f"Unknown aspect ratio: {patch['aspect']}")
    if "quality" in patch and patch["quality"] not in platforms.QUALITY:
        raise HTTPException(400, f"Unknown quality preset: {patch['quality']}")
    if "layout" in patch and patch["layout"] not in ("standard", "gameplay_top", "gameplay_full"):
        raise HTTPException(400, f"Unknown layout: {patch['layout']}")


@app.patch("/api/projects/{pid}")
def patch_project(pid: str, payload: dict = Body(...)):
    project = library.require(pid)
    if isinstance(payload.get("options"), dict):
        _check_options(payload["options"])
    history.record(project, payload.get("_label") or "settings change")
    if "options" in payload:
        project["options"] = {**(project.get("options") or {}), **payload["options"]}
    if "title" in payload:
        project["title"] = str(payload["title"])[:200]
    if "style" in payload:
        project["style"] = CaptionStyle.from_dict(payload["style"]).to_dict()
    library.save(project)
    return project


@app.post("/api/projects/{pid}/reanalyze")
def reanalyze(pid: str, payload: dict = Body(default={})):
    project = library.require(pid)
    history.record(project, "re-analyze video")
    project.pop("transcript", None)
    if payload:
        project["options"] = {**(project.get("options") or {}), **payload}
    library.save(project)
    job = jobs.create("analyze", title=project["title"], project_id=pid)
    jobs.start(job["id"], lambda jid: analyzer.analyze(pid, jid))
    return {"job_id": job["id"]}


@app.post("/api/projects/{pid}/detect")
def redetect(pid: str, payload: dict = Body(default={})):
    """Re-run only clip selection using the cached transcript."""
    from .pipeline import llm, text as txt
    project = library.require(pid)
    transcript = project.get("transcript")
    if not transcript:
        raise HTTPException(400, "Transcribe the video first")
    history.record(project, "re-detect clips")
    opts = {**library.default_options(), **(project.get("options") or {}), **(payload or {})}
    project["options"] = opts
    clips, provider = llm.detect_clips(
        transcript["words"], count=int(opts["clip_count"]), settings=store.load_settings(),
        min_len=float(opts["clip_min"]), max_len=float(opts["clip_max"]),
        provider=opts.get("provider", "auto"),
    )
    info = project["media"]
    src = Path(project["video_path"])
    out = []
    for i, clip in enumerate(clips):
        start, end = txt.snap_boundaries(transcript["words"], clip["start"], clip["end"])
        clip.update({"start": start, "end": end, "id": f"c{i + 1}",
                     "words": txt.words_between(transcript["words"], start, end),
                     "style_preset": "bold_impact"})
        try:
            from .pipeline import reframe
            clip["crop_plan"] = reframe.analyze(src, start, end, info["width"], info["height"])
        except Exception:
            clip["crop_plan"] = None
        out.append(clip)
    project["clips"] = out
    project["analysis_provider"] = provider
    library.save(project)
    return {"clips": out, "provider": provider}


# ----------------------------------------------------------------- clips ----
@app.patch("/api/projects/{pid}/clips/{cid}")
def patch_clip(pid: str, cid: str, payload: dict = Body(...)):
    project = library.require(pid)
    clip = next((c for c in project.get("clips", []) if c["id"] == cid), None)
    if not clip:
        raise HTTPException(404, "Clip not found")
    history.record(project, payload.get("_label") or f"edit {cid}")
    if "start" in payload or "end" in payload:
        try:
            start = float(payload.get("start", clip["start"]))
            end = float(payload.get("end", clip["end"]))
        except (TypeError, ValueError):
            raise HTTPException(400, "Clip start/end must be numbers")
        total = float((project.get("media") or {}).get("duration") or end)
        if end > total + 0.05 or start < 0:
            raise HTTPException(400, "Clip must stay inside the source video")
        if end - start < 1:
            raise HTTPException(400, "Clip must be at least 1 second")
        transcript = project.get("transcript") or {}
        words = transcript.get("words") or []
        if words:
            from .pipeline import text as txt
            start, end = txt.snap_boundaries(words, start, end)
            clip["words"] = txt.words_between(words, start, end)
            clip["words_override"] = None
        clip["start"], clip["end"] = start, end
    if "style" in payload:
        clip["style"] = CaptionStyle.from_dict(payload["style"]).to_dict()
    if "style_preset" in payload:
        clip["style_preset"] = payload["style_preset"]
        clip["style"] = style_for(payload["style_preset"]).to_dict()
    if "title" in payload:
        clip["title"] = str(payload["title"])[:160]
    if "words_override" in payload:
        clip["words_override"] = payload["words_override"]
    if "crop_plan" in payload:
        clip["crop_plan"] = payload["crop_plan"]
    if "reframe" in payload:
        clip["reframe"] = payload["reframe"]
    if "clear_export" in payload:
        clip.pop("export", None)
    library.save(project)
    return clip


@app.post("/api/projects/{pid}/clips")
def add_clip(pid: str, payload: dict = Body(default={})):
    project = library.require(pid)
    transcript = project.get("transcript") or {}
    words = transcript.get("words") or []
    total = float((project.get("media") or {}).get("duration") or 0)
    try:
        start = max(0.0, float(payload.get("start") or 0))
        end = float(payload.get("end") or min(total or start + 30, start + 30))
    except (TypeError, ValueError):
        raise HTTPException(400, "Clip start/end must be numbers")
    if total:
        end = min(end, total)
    if end - start < 1:
        raise HTTPException(400, "Clip must be at least 1 second")
    history.record(project, "add clip")
    from .pipeline import text as txt
    if words:
        start, end = txt.snap_boundaries(words, start, end)
    cid = jobs.new_id("c")
    clip = {"id": cid, "start": start, "end": end,
            "title": payload.get("title") or "New clip", "score": None,
            "reason": "Manually added", "words": txt.words_between(words, start, end) if words else [],
            "style_preset": "bold_impact", "crop_plan": None, "provider": "manual"}
    project.setdefault("clips", []).append(clip)
    library.save(project)
    return clip


@app.delete("/api/projects/{pid}/clips/{cid}")
def delete_clip(pid: str, cid: str):
    project = library.require(pid)
    history.record(project, "delete clip")
    # remove the rendered thumbnail too, so a deleted clip leaves nothing behind
    (library.exports_dir(pid) / f"{cid}.jpg").unlink(missing_ok=True)
    project["clips"] = [c for c in project.get("clips", []) if c["id"] != cid]
    library.save(project)
    return {"ok": True}


# ----------------------------------------------------------------- brand ----
@app.get("/api/brand")
def get_brand():
    return store.load_brand()


@app.put("/api/brand")
def put_brand(payload: dict = Body(...)):
    return store.save_brand(payload)


@app.post("/api/projects/{pid}/brand")
def apply_brand(pid: str):
    """Apply the saved brand kit (logo, colours, font) to a project."""
    project = library.require(pid)
    brand = store.load_brand()
    history.record(project, "apply brand kit")
    opts = project.setdefault("options", {})
    wm = brand.get("watermark") or {}
    if brand.get("logo_path") and Path(brand["logo_path"]).exists():
        opts["watermark_path"] = brand["logo_path"]
        opts["watermark_position"] = wm.get("position") or opts.get("watermark_position") or "top-right"
        opts["watermark_width"] = int(wm.get("width") or opts.get("watermark_width") or 180)
        opts["watermark_opacity"] = float(wm.get("opacity") or opts.get("watermark_opacity") or 0.85)
    colours = brand.get("colors") or []
    font = (brand.get("fonts") or [None])[0]
    for clip in project.get("clips", []):
        style = dict(clip.get("style") or {})
        if colours:
            style["primary_color"] = colours[0]
        if len(colours) > 1:
            style["highlight_color"] = colours[1]
        if font:
            style["font"] = font
        if style:
            clip["style"] = CaptionStyle.from_dict(style).to_dict()
    library.save(project)
    return project


# --------------------------------------------------------------- presets ----
@app.get("/api/platforms")
def get_platforms():
    return platforms.list_presets()


# --------------------------------------------------------------- history ----
@app.get("/api/projects/{pid}/history")
def get_history(pid: str):
    library.require(pid)
    return history.history_info(pid)


@app.post("/api/projects/{pid}/history/{action}")
def history_action(pid: str, action: str):
    project = library.require(pid)
    if action not in ("undo", "redo"):
        raise HTTPException(400, "action must be undo or redo")
    try:
        project = history.undo(project) if action == "undo" else history.redo(project)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    library.save(project)
    return {"project": project, **history.history_info(pid)}


# ----------------------------------------------------------------- social ----
@app.post("/api/projects/{pid}/clips/{cid}/social")
def clip_social(pid: str, cid: str, payload: dict = Body(default={})):
    project = library.require(pid)
    clip = next((c for c in project.get("clips", []) if c["id"] == cid), None)
    if not clip:
        raise HTTPException(404, "Clip not found")
    platform = payload.get("platform") or (project.get("options") or {}).get("platform") or "reels"
    words = clip.get("words_override") or clip.get("words") or []
    result = social.suggest(words, clip.get("title") or "", platform, store.load_settings())
    return result


# ---------------------------------------------------------------- render ----
@app.post("/api/projects/{pid}/clips/{cid}/preview-frame")
def preview_frame(pid: str, cid: str, payload: dict = Body(default={})):
    project = library.require(pid)
    clip = next((c for c in project.get("clips", []) if c["id"] == cid), None)
    if not clip:
        raise HTTPException(404, "Clip not found")
    style = CaptionStyle.from_dict(payload.get("style") or clip.get("style") or style_for(clip.get("style_preset")))
    at = float(payload.get("at") or 0)
    at = max(0.0, min(at, max(0.0, clip["end"] - clip["start"] - 0.1)))
    if payload.get("words_override"):
        clip = {**clip, "words_override": payload["words_override"]}
    out = config.JOBS / f"preview-{pid}-{cid}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        render_preview_frame(project, clip, style, at, out, out.parent)
    except Exception as exc:
        raise HTTPException(500, f"Preview failed: {exc}")
    return FileResponse(out, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})


@app.post("/api/projects/{pid}/clips/{cid}/plan")
def clip_plan(pid: str, cid: str):
    """The edit plan used by the renderer, so the preview matches the export.

    Returns the kept ranges (silence/filler removal), the edited duration and
    caption words already remapped onto that timeline.
    """
    from .pipeline import audio as aud

    project = library.require(pid)
    clip = next((c for c in project.get("clips", []) if c["id"] == cid), None)
    if not clip:
        raise HTTPException(404, "Clip not found")
    info = project.get("media") or {}
    duration = max(0.4, float(clip["end"]) - float(clip["start"]))
    words = clip.get("words_override") or clip.get("words") or []
    local = [{**w, "start": max(0.0, float(w["start"]) - float(clip["start"])),
              "end": max(0.0, float(w["end"]) - float(clip["start"]))} for w in words]
    local = [w for w in local if w["end"] > 0 and w["start"] < duration]
    opts = project.get("options") or {}
    keep = aud.build_keep_ranges(local, duration, bool(opts.get("remove_silences")),
                                 bool(opts.get("remove_fillers")))
    edited = max(0.5, sum(b - a for a, b in keep))
    style = CaptionStyle.from_dict(clip.get("style") or project.get("style") or {})
    if not clip.get("style") and not project.get("style"):
        style = style_for(clip.get("style_preset") or "bold_impact")
    mapped = aud.remap_words([{**w, "start": w["start"], "end": w["end"]} for w in local], keep)
    mapped = [{**w, "start": max(0.0, min(edited, w["start"])),
               "end": max(0.0, min(edited, w["end"]))} for w in mapped]
    mapped = [w for w in mapped if w["end"] > w["start"] + 0.02]
    return {
        "keep_ranges": keep,
        "raw_duration": round(duration, 3),
        "duration": round(edited, 3),
        "removed": round(max(0.0, duration - edited), 3),
        "words": mapped,
        "width": info.get("width"),
        "height": info.get("height"),
    }


@app.get("/api/projects/{pid}/clips/{cid}/ass")
def clip_ass(pid: str, cid: str, at: float = 0):
    project = library.require(pid)
    clip = next((c for c in project.get("clips", []) if c["id"] == cid), None)
    if not clip:
        raise HTTPException(404, "Clip not found")
    style = CaptionStyle.from_dict(clip.get("style") or style_for(clip.get("style_preset")))
    words = [{**w, "start": w["start"] - clip["start"], "end": w["end"] - clip["start"]}
             for w in (clip.get("words") or [])]
    return JSONResponse({"ass": build_ass(words, style, clip["end"] - clip["start"])})


@app.post("/api/projects/{pid}/clips/{cid}/export")
def export_clip(pid: str, cid: str, payload: dict = Body(default={})):
    project = library.require(pid)
    if payload.get("style"):
        library.require(pid)
        clip = next((c for c in project["clips"] if c["id"] == cid), None)
        if clip:
            clip["style"] = CaptionStyle.from_dict(payload["style"]).to_dict()
            library.save(project)
    # one export per clip at a time — a second concurrent write used to corrupt the file
    if jobs.running_for(pid):
        raise HTTPException(409, "An export is already running for this project. Wait for it to finish.")
    job = jobs.create("export", title=f"Export {cid}", project_id=pid, clip_id=cid)
    jobs.start(job["id"], lambda jid: exporter.export_one(pid, cid, jid))
    return {"job_id": job["id"]}


@app.post("/api/projects/{pid}/export")
def export_batch(pid: str, payload: dict = Body(default={})):
    project = library.require(pid)
    ids = payload.get("clip_ids") or [c["id"] for c in project.get("clips", [])]
    if not ids:
        raise HTTPException(400, "No clips to export")
    if isinstance(payload.get("style"), dict):
        style = CaptionStyle.from_dict(payload["style"]).to_dict()
        history.record(project, "batch style")
        for clip in project["clips"]:
            if clip["id"] in ids:
                clip["style"] = style
        library.save(project)
    job = jobs.create("batch", title="Batch export", project_id=pid, clip_ids=ids)
    jobs.start(job["id"], lambda jid: exporter.export_many(pid, ids, jid))
    return {"job_id": job["id"]}


# ------------------------------------------------------------------- jobs ----
@app.get("/api/jobs")
def list_jobs():
    out = []
    for f in config.JOBS.glob("*/job.json"):
        data = store.read_json(f)
        if data:
            out.append(jobs.public(data))
    return {"jobs": sorted(out, key=lambda j: j.get("created") or 0, reverse=True)[:40]}


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    job = jobs.get(jid)
    if not job:
        raise HTTPException(404, "Job not found")
    return jobs.public(job)


# ------------------------------------------------------------------ files ----
@app.get("/api/projects/{pid}/files/{name}")
def project_file(pid: str, name: str):
    root = library.project_dir(pid).resolve()
    path = (root / name).resolve()
    if not str(path).startswith(str(root)) or not path.exists():
        # clip thumbnails live next to the exports, not in the project folder
        alt_root = library.exports_dir(pid).resolve()
        alt = (alt_root / name).resolve()
        if str(alt).startswith(str(alt_root)) and alt.exists():
            mime = mimetypes.guess_type(alt.name)[0] or "application/octet-stream"
            return FileResponse(alt, media_type=mime,
                                headers={"Cache-Control": "max-age=60"})
        raise HTTPException(404, "Not found")
    return FileResponse(path, headers={"Cache-Control": "max-age=60"})


@app.get("/api/projects/{pid}/exports")
def list_exports(pid: str):
    """Every rendered file still on disk, newest first.

    The editor used to offer downloads only from the live export job, so the
    link disappeared after a reload and the file looked missing. This list is
    the durable view of the exports folder.
    """
    project = library.require(pid)
    titles = {(c.get("export") or {}).get("file"): c.get("title") for c in project.get("clips", [])}
    root = library.exports_dir(pid)
    files = []
    if root.exists():
        for path in root.glob("*.mp4"):
            if not path.is_file():
                continue
            stat = path.stat()
            files.append({
                "name": path.name,
                "url": f"/api/projects/{pid}/exports/{path.name}",
                "size": stat.st_size,
                "created": stat.st_mtime,
                "title": titles.get(path.name) or path.stem.replace("-", " "),
            })
    files.sort(key=lambda f: f["created"], reverse=True)
    return {"files": files}


@app.get("/api/projects/{pid}/exports/{name}")
def export_file(pid: str, name: str):
    path = (library.exports_dir(pid) / name).resolve()
    if not str(path).startswith(str(library.exports_dir(pid).resolve())) or not path.exists():
        raise HTTPException(404, "Not found")
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    # Attachment so the browser saves the file to the user's own computer
    # instead of trying to play it inline.
    return FileResponse(
        path,
        media_type=mime,
        filename=name,
        content_disposition_type="attachment",
    )


@app.get("/api/projects/{pid}/source")
def source_file(pid: str):
    project = library.require(pid)
    path = Path(project["video_path"])
    if not path.exists():
        raise HTTPException(404, "Source missing")
    return FileResponse(path, media_type="video/mp4")


# ------------------------------------------------------------------- SPA ----
if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
else:
    @app.get("/")
    def index():
        return JSONResponse({"message": "UI not built yet. Run the web dev server."})
