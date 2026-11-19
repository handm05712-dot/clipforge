"""Stage F — cut, reframe and caption one chosen clip.

The point of this module is *not* to render a second time in a new style. It
builds the exact project/clip shape that `render.render_clip` already
understands, so viral clips get the same reframe maths, the same safe-area
clamping and the same ASS caption pipeline as everything else in ClipForge.

Flow for one clip:

  1. download only [start-pad, end+pad] with yt-dlp  (cached per range)
  2. probe it, and treat the local file as the project source
  3. re-transcribe just that segment for accurate word times
  4. plan the face crop on those local times
  5. render_clip at 1080x1920 with the Neon Keyword preset
  6. optionally burn a credit line over the bottom of the frame
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ... import config, jobs, library
from .. import downloader, media, reframe, transcribe, ytlink
from ..captions.presets import style_for
from ..render import render_clip

# The preset named "Neon Keyword" in the UI is the existing neon_glow style.
DEFAULT_PRESET = "neon_glow"
PAD = 2.0


class RenderError(RuntimeError):
    pass


def _range_key(start: float, end: float) -> str:
    return f"{int(round(start * 10)):07d}-{int(round(end * 10)):07d}"


def segment(info: dict, clip: dict, workdir: Path, on_stage) -> Path:
    """Download only this clip's span (with padding), cached by range.

    A cached file is reused on re-render, which is what makes "Regenerate" and
    "Render all" cheap: the expensive part is downloading, not encoding.
    """
    start = max(0.0, float(clip["start"]) - PAD)
    end = float(clip["end"]) + PAD
    if info.get("local_path") and Path(info["local_path"]).exists():
        return Path(info["local_path"])     # recorded live / uploaded fallback

    folder = config.DATA / "viral" / info["video_id"] / "segments"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{_range_key(start, end)}.mp4"
    if out.exists() and out.stat().st_size > 1024:
        return out

    on_stage("Cutting", f"Downloading {end - start:.0f}s of source video…")
    try:
        return downloader.download_segment(
            info.get("url") or ytlink.watch_url(info["video_id"]),
            start, end, out,
            on_progress=lambda p, m: on_stage("Cutting", m),
        )
    except downloader.DownloadError as exc:
        raise RenderError(str(exc)) from exc


def local_words(info: dict, clip: dict, src: Path, workdir: Path, on_stage) -> list[dict]:
    """Word times inside the downloaded, padded segment.

    Always re-transcribes: source captions are timed against the whole video,
    and a 60 s clip deserves exact timings rather than a proportional guess.
    Cached per range so a second render is instant.
    """
    key = _range_key(clip["start"], clip["end"])
    cached = workdir / f"words-{key}.json"
    if cached.exists():
        try:
            return json.loads(cached.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    on_stage("Listening closely", "Re-transcribing this clip for exact word timing…")
    audio = workdir / f"clip-{key}.wav"
    media.extract_audio(src, audio, duration=float(clip["end"]) - float(clip["start"]) + 4,
                        on_progress=lambda p: on_stage("Listening closely",
                                                       f"Extracting audio… {p * 100:.0f}%"))
    result = transcribe.transcribe(audio, on_progress=lambda p, m: on_stage("Listening closely", m))
    audio.unlink(missing_ok=True)
    words = result.get("words") or []
    cached.write_text(json.dumps(words), encoding="utf-8")
    return words


def build_project(info: dict, clip: dict, src: Path, words: list[dict],
                  opts: dict) -> tuple[dict, dict]:
    """The project/clip pair `render_clip` expects, in local time.

    Segment files start at the padded download point, so every timestamp is
    shifted back by that offset. Getting this wrong is the classic "clip starts
    two seconds late" bug, so it is done in one place here.
    """
    offset = max(0.0, float(clip["start"]) - PAD)
    local = [
        {"text": w["text"], "start": round(max(0.0, float(w["start"]) - offset), 3),
         "end": round(max(0.0, float(w["end"]) - offset), 3)}
        for w in words
    ]
    local = [w for w in local if w["end"] > 0]
    probed = media.probe(src)

    project = {
        "id": "viral",
        "title": info.get("title") or "Viral clip",
        "source": info.get("url") or "",
        "video_path": str(src),
        "media": probed,
        "options": {
            **library.default_options(),
            "reframe": opts.get("reframe") or "face",
            "remove_silences": False,      # keep the clip's chosen boundaries
            "remove_fillers": True,
            "voice_boost": True,
            "platform": "reels",           # 9:16, 1080x1920
            "aspect": "9:16",
            "quality": opts.get("quality") or "high",
            "layout": "standard",
            "clip_min": 20, "clip_max": 60, "clip_count": 1,
        },
        "style": None,
        "clips": [],
        "created": 0,
        "status": "ready",
        "poster": None,
    }
    clip_out = {
        "id": "v",
        "start": offset,
        "end": offset + (float(clip["end"]) - float(clip["start"])) + PAD,
        "title": clip.get("title") or "",
        "words": local,
        "style": None,
        "style_preset": opts.get("preset") or DEFAULT_PRESET,
        "crop_plan": None,
        "wide": False,
    }
    if opts.get("credit"):
        project["options"]["credit_line"] = opts["credit"]
    return project, clip_out


def track_faces(project: dict, clip: dict, on_stage) -> None:
    """Plan the 9:16 crop once per clip; fall back to a still centre crop."""
    src = Path(project["video_path"])
    info = project["media"]
    on_stage("Framing", "Following the speaker's face…")
    try:
        clip["crop_plan"] = reframe.analyze(
            src, clip["start"], clip["end"], info["width"], info["height"]
        )
    except Exception:
        clip["crop_plan"] = reframe.still_crop(info["width"], info["height"])
    clip["wide"] = bool(clip.get("crop_plan", {}).get("wide"))


def render_viral_clip(info: dict, clip: dict, job_id: str,
                      opts: dict | None = None, on_stage=None) -> dict:
    """The whole of stage F for one clip. Returns {path, url, size, duration}."""
    opts = opts or {}

    def stage(name: str, message: str) -> None:
        if on_stage:
            on_stage(name, message)

    workdir = config.JOBS / job_id / "viral-render" / clip["id"]
    workdir.mkdir(parents=True, exist_ok=True)

    src = segment(info, clip, workdir, stage)
    words = local_words(info, clip, src, workdir, stage)
    project, built = build_project(info, clip, src, words, opts)
    track_faces(project, built, stage)

    out = workdir / "out.mp4"
    stage("Rendering", "Burning Neon Keyword captions at 1080×1920…")
    result = render_clip(project, built, out, workdir,
                         on_progress=lambda p: stage("Rendering", f"Encoding… {p * 100:.0f}%"))
    if not out.exists() or out.stat().st_size < 1024:
        raise RenderError("The render produced no video data.")
    if opts.get("credit"):
        burned = workdir / "out-credit.mp4"
        burn_credit(out, burned, opts["credit"], stage)
        if burned.exists():
            shutil.move(str(burned), str(out))

    # keep the finished file next to the project's normal exports so the
    # existing /exports listing and download links work unchanged
    out_dir = config.EXPORTS / f"viral-{info['video_id']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(clip.get("title") or clip["id"])
    final = out_dir / f"{clip['id']}-{slug}.mp4"
    shutil.copyfile(out, final)
    return {
        "path": str(final),
        "url": f"/api/viral/files/{final.name}",
        "size": final.stat().st_size,
        "duration": result.get("duration"),
        "width": result.get("width"),
        "height": result.get("height"),
    }


def burn_credit(src: Path, out: Path, text: str, stage) -> None:
    """Small credit line above the platform's bottom safe area."""
    fonts_dir = config.FONTS
    safe = re.sub(r"[\\':]", "", str(text))[:90]
    vf = (
        "drawbox=x=0:y=ih*0.86:w=iw:h=ih*0.05:color=black@0.45:t=fill,"
        f"drawtext=text='{safe}':fontcolor=white@0.9:fontsize=34:"
        f"x=(w-text_w)/2:y=ih*0.875"
    )
    stage("Rendering", "Adding the credit line…")
    media.run(["-y", "-i", str(src), "-vf", vf, "-c:v", "libx264", "-crf", "18",
               "-preset", "veryfast", "-c:a", "copy", str(out)],
              cwd=fonts_dir)


def _slug(text: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else "" for c in (text or "")).strip()
    return ("-".join(keep.split())[:48].lower()) or "clip"


def export_all(info: dict, clips: list[dict], job_id: str, opts: dict | None = None) -> list[dict]:
    """Batch render, used by "Render all". One failure does not stop the rest."""
    total = max(1, len(clips))
    files = []
    for i, clip in enumerate(clips):
        def stage(name: str, message: str) -> None:
            jobs.update(job_id, (i + 0.5) / total, name,
                        f"Clip {i + 1}/{total} — {message}")
        try:
            result = render_viral_clip(info, clip, f"{job_id}-{i}", opts, stage)
            files.append({"clip_id": clip["id"], **result})
        except Exception as exc:  # noqa: BLE001 - report and continue
            files.append({"clip_id": clip["id"], "error": str(exc)[:300]})
    return files
