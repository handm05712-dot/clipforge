"""Phase 1 pipeline: ingest, transcribe, detect clips, plan reframes."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .. import config, jobs, library, store
from . import llm, media, reframe, text as txt, transcribe

YT_URL_RE = re.compile(r"^https?://(www\.)?(youtube\.com|youtu\.be|m\.youtube\.com)/", re.I)


def ytdlp_command() -> list[str] | None:
    """Return the argv prefix for yt-dlp, whether it is a binary or the module."""
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    try:
        import importlib.util

        if importlib.util.find_spec("yt_dlp") is not None:
            return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        pass
    return None


def ytdlp_available() -> bool:
    return ytdlp_command() is not None


def _cookies_args() -> list[str]:
    """Optional cookie file for age/region restricted or bot-checked videos."""
    cookies = config.DATA / "cookies.txt"
    if cookies.exists() and cookies.stat().st_size > 0:
        return ["--cookies", str(cookies)]
    return []


def ingest_youtube(url: str, job_id: str) -> Path:
    prefix = ytdlp_command()
    if not prefix:
        raise RuntimeError(
            "yt-dlp is not installed. Install it with:  pip install -r requirements.txt"
        )
    dest = config.UPLOADS / f"{job_id}.mp4"
    cmd = [
        *prefix, url,
        # prefer H.264/AAC so the browser preview can play the source
        "-f", "bv*[height<=1080][ext=mp4][vcodec^=avc1]+ba[ext=m4a]/"
              "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*+ba/b",
        "--merge-output-format", "mp4",
        "-o", str(dest),
        "--no-playlist",
        "--restrict-filenames",
        "--newline",
        "--no-warnings",
        "--retries", "3",
        *_cookies_args(),
    ]
    jobs.update(job_id, 0.05, "Downloading", "Fetching video with yt-dlp…")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail: list[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.strip()
        if not line:
            continue
        tail.append(line)
        tail = tail[-12:]
        m = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
        if m:
            pct = float(m.group(1)) / 100
            jobs.update(job_id, 0.02 + pct * 0.55, "Downloading",
                        f"Downloading… {m.group(1)}%")
        elif "[Merger]" in line or "[ExtractAudio]" in line:
            jobs.update(job_id, 0.6, "Downloading", "Muxing audio and video…")
    code = proc.wait()
    if code != 0:
        message = " | ".join(tail[-3:])[:420]
        lower = message.lower()
        if "sign in to confirm" in lower or "not a bot" in lower:
            message += (
                "\n\nYouTube wants to verify this machine is not a bot. "
                "Export cookies from a signed-in browser and save them as "
                "data/cookies.txt, then retry. See the README troubleshooting section."
            )
        elif "private video" in lower or "members-only" in lower:
            message += "\n\nThis video is private or members-only; cookies are required."
        elif "unavailable" in lower or "removed" in lower:
            message += "\n\nThis video is unavailable in your region or was removed."
        raise RuntimeError(f"Download failed: {message}")
    if not dest.exists():
        candidates = sorted(
            (p for p in config.UPLOADS.glob(f"{job_id}*") if p.suffix.lower() in
             {".mp4", ".mkv", ".webm", ".mov"}),
            key=lambda p: p.stat().st_size, reverse=True,
        )
        if not candidates:
            raise RuntimeError("Download finished but no video file was found.")
        dest = candidates[0]
    jobs.update(job_id, 0.65, "Downloading", "Download complete")
    return dest


def analyze(project_id: str, job_id: str) -> None:
    project = library.require(project_id)
    src = Path(project["video_path"]).resolve()
    info = project.get("media") or media.probe(src)
    project["media"] = info
    jobs.update(job_id, 0.03, "Preparing", "Reading media…")

    # 1. audio
    audio_path = library.project_dir(project_id) / "audio.wav"
    if not audio_path.exists():
        media.extract_audio(src, audio_path, duration=info["duration"],
                            on_progress=lambda p: jobs.update(job_id, 0.03 + p * 0.10,
                                                              "Extracting audio",
                                                              "Decoding audio track…"))

    # 2. transcription
    jobs.update(job_id, 0.15, "Transcribing", "Running faster-whisper…")
    t0 = time.time()
    transcript = transcribe.transcribe(
        audio_path,
        on_progress=lambda p, msg: jobs.update(job_id, 0.15 + p * 0.45, "Transcribing", msg),
        duration=info["duration"],
    )
    project["transcript"] = transcript
    project["status"] = "transcribed"
    library.save(project)
    jobs.update(job_id, 0.62, "Transcribed",
                f"{len(transcript['words'])} words in {time.time() - t0:.1f}s "
                f"({transcript['language']})")

    # 3. clip detection
    opts = {**library.default_options(), **(project.get("options") or {})}
    settings = store.load_settings()
    jobs.update(job_id, 0.66, "Finding clips", "Scoring candidate segments…")
    clips, provider = llm.detect_clips(
        transcript["words"], count=int(opts["clip_count"]), settings=settings,
        min_len=float(opts["clip_min"]), max_len=float(opts["clip_max"]),
        provider=opts.get("provider", "auto"),
    )
    jobs.update(job_id, 0.70, "Finding clips",
                f"{len(clips)} clips via {provider}. Planning reframes…")

    # 4. reframe plans + thumbnails
    exports = library.exports_dir(project_id)
    built = []
    for i, clip in enumerate(clips):
        start, end = txt.snap_boundaries(transcript["words"], clip["start"], clip["end"])
        clip["start"], clip["end"] = start, end
        clip["id"] = f"c{i + 1}"
        clip["words"] = txt.words_between(transcript["words"], start, end)
        jobs.update(job_id, 0.70 + 0.24 * (i / max(1, len(clips))), "Reframing",
                    f"Tracking faces in clip {i + 1}/{len(clips)}…")
        try:
            clip["crop_plan"] = reframe.analyze(src, start, end, info["width"], info["height"])
        except Exception as exc:
            print(f"[reframe] {exc}")
            clip["crop_plan"] = reframe.still_crop(info["width"], info["height"])
        clip["wide"] = bool(clip["crop_plan"].get("wide"))
        thumb = exports / f"{clip['id']}.jpg"
        try:
            media.thumbnail(src, start + min(2.0, (end - start) / 2), thumb, width=420)
            clip["thumb"] = f"/api/projects/{project_id}/exports/{thumb.name}"
        except Exception:
            clip["thumb"] = None
        clip["style_preset"] = clip.get("style_preset") or "bold_impact"
        built.append(clip)

    project["clips"] = built
    project["status"] = "ready"
    project["analysis_provider"] = provider
    library.save(project)
    jobs.update(job_id, 0.99, "Saving", "Finishing up…")
    jobs.patch(job_id, clip_count=len(built), provider=provider,
               language=transcript.get("language"), duration=info["duration"])
