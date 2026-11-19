"""The one place that talks to yt-dlp.

Swap or extend this module and the rest of the app keeps working: everything
else asks for metadata, a transcript, a clip segment or an audio track and gets
plain Python data back.

Nothing here imports the yt-dlp Python API. Using the CLI keeps the version you
have installed as the source of truth, and makes an upgrade a one-line change.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from .. import config

# Bump when YouTube changes something and the fix is a newer yt-dlp. The
# health endpoint reports whether the installed build is older than this.
MIN_VERSION = "2025.01.01"
RECOMMENDED = "pip install -U 'yt-dlp>=2025.1.15'"


class DownloadError(RuntimeError):
    """A failure we can explain to the user, with a way forward."""

    def __init__(self, message: str, reason: str = "unknown", hint: str = ""):
        super().__init__(message)
        self.reason = reason
        self.hint = hint

    def as_dict(self) -> dict:
        return {"message": str(self), "reason": self.reason, "hint": self.hint}


def command() -> list[str] | None:
    """argv prefix for yt-dlp: the binary if present, else the module."""
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    try:
        import importlib.util

        if importlib.util.find_spec("yt_dlp") is not None:
            return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        pass
    return None


def available() -> bool:
    return command() is not None


def version() -> str | None:
    prefix = command()
    if not prefix:
        return None
    try:
        out = subprocess.run([*prefix, "--version"], capture_output=True, text=True, timeout=20)
        return (out.stdout or "").strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def version_ok() -> bool:
    """False when the installed yt-dlp is older than MIN_VERSION."""
    got = version()
    if not got:
        return False
    nums = re.findall(r"\d+", got)
    want = re.findall(r"\d+", MIN_VERSION)
    if not nums:
        return False
    # compare as zero-padded tuples so 2025.1.5 == 2025.01.05
    width = max(len(nums), len(want))
    a = tuple(int(n) for n in nums[:width] + ["0"] * (width - len(nums)))
    b = tuple(int(n) for n in want[:width] + ["0"] * (width - len(want)))
    return a >= b


def upgrade_command() -> str:
    return RECOMMENDED


def _env_args() -> list[str]:
    """Cookies and proxy come from env or files, never from the UI."""
    args: list[str] = []
    cookie_env = os.environ.get("YTDLP_COOKIES", "").strip()
    if cookie_env:
        args += ["--cookies", cookie_env]
    else:
        cookie_file = config.DATA / "cookies.txt"
        if cookie_file.exists() and cookie_file.stat().st_size > 0:
            args += ["--cookies", str(cookie_file)]

    cookies_from_browser = os.environ.get("YTDLP_COOKIES_FROM_BROWSER", "").strip()
    if cookies_from_browser:
        args += ["--cookies-from-browser", cookies_from_browser]

    proxy = os.environ.get("YTDLP_PROXY", "").strip()
    if proxy:
        args += ["--proxy", proxy]
    return args


def base_args() -> list[str]:
    return [
        "--no-playlist",
        "--no-warnings",
        "--newline",
        "--no-progress" if os.environ.get("CLIPFORGE_QUIET") else "--progress",
        "--socket-timeout", "20",
        *_env_args(),
    ]


# ------------------------------------------------------------------ run ----

Progress = Callable[[float, str], None]


def _run(args: list[str], on_progress: Progress | None = None,
         duration: float | None = None, tries: int = 3, label: str = "yt-dlp") -> str:
    """Run yt-dlp with backoff, streaming progress and keeping failure detail."""
    prefix = command()
    if not prefix:
        raise DownloadError(
            "yt-dlp is not installed, so links cannot be fetched.",
            reason="no_ytdlp",
            hint="Install it with: pip install -r requirements.txt",
        )
    last_tail: list[str] = []
    delay = 2.0
    for attempt in range(1, tries + 1):
        cmd = [*prefix, *base_args(), *args]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        tail: list[str] = []
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line.strip():
                continue
            tail.append(line)
            tail = tail[-30:]
            if on_progress:
                pct = _percent(line, duration)
                if pct is not None:
                    on_progress(pct, f"{label}… {pct * 100:.0f}%")
        code = proc.wait()
        if code == 0:
            return "\n".join(tail)
        last_tail = tail
        # a 403 from an expired signature is worth one immediate retry
        text = "\n".join(tail).lower()
        if attempt < tries:
            time.sleep(delay)
            delay *= 2
            if "sign in to confirm" not in text:
                continue
            # bot checks do not get better with waiting
            break
    raise classify(last_tail)


def _percent(line: str, duration: float | None) -> float | None:
    m = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
    if m:
        return min(1.0, float(m.group(1)) / 100)
    # for --download-sections yt-dlp prints the clip time instead of a %
    m = re.search(r"\[download\]\s+(\d+:\d+(?::\d+)?)\s+of\s+(\d+:\d+(?::\d+)?)", line)
    if m:
        done, total = _hms(m.group(1)), _hms(m.group(2))
        if total:
            return min(1.0, done / total)
    return None


def _hms(text: str) -> float:
    parts = [float(p) for p in text.split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


SIGNATURES: list[tuple[str, str, str, str]] = [
    # (match text, reason, message, hint)
    (
        "sign in to confirm",
        "bot_check",
        "YouTube asked this machine to prove it is not a bot.",
        "Save signed-in browser cookies to data/cookies.txt, or set YTDLP_COOKIES to a "
        "cookies file. On a cloud server, a cookie file or a residential proxy "
        "(YTDLP_PROXY) is usually required.",
    ),
    (
        "not a bot",
        "bot_check",
        "YouTube asked this machine to prove it is not a bot.",
        "Export cookies from a signed-in browser to data/cookies.txt and retry.",
    ),
    (
        "private video",
        "private",
        "This video is private.",
        "Only the channel owner can download it. Upload the file instead.",
    ),
    (
        "members-only",
        "members_only",
        "This video is for channel members only.",
        "Sign in with a member account via cookies, or upload the file.",
    ),
    (
        "age-restricted",
        "age_restricted",
        "This video is age-restricted.",
        "Use a signed-in cookies file, or upload the file.",
    ),
    (
        "confirm your age",
        "age_restricted",
        "This video is age-restricted.",
        "Use a signed-in cookies file, or upload the file.",
    ),
    (
        "not available in your country",
        "region",
        "This video is blocked in this server's region.",
        "A proxy in the right region (YTDLP_PROXY) can help. Otherwise upload the file.",
    ),
    (
        "removed by the uploader",
        "removed",
        "The uploader removed this video.",
        "",
    ),
    (
        "video unavailable",
        "unavailable",
        "YouTube says this video is unavailable.",
        "It may be deleted, region-locked, or a members-only release. Upload the file instead.",
    ),
    (
        "is live",
        "is_live",
        "This stream is live right now, so there is no finished video to download yet.",
        "Use “Analyze what has been streamed so far” to capture the part that already aired.",
    ),
    (
        "premiere",
        "premiere",
        "This video has not premiered yet.",
        "Come back when it starts, or upload the file after it airs.",
    ),
]


def classify(lines: list[str] | str) -> DownloadError:
    """Turn yt-dlp's output into a message the user can act on."""
    text = lines if isinstance(lines, str) else "\n".join(lines)
    low = text.lower()
    for needle, reason, message, hint in SIGNATURES:
        if needle in low:
            detail = _detail(text)
            return DownloadError(message + (f" ({detail})" if detail else ""), reason, hint)
    detail = _detail(text) or "No further detail from yt-dlp."
    return DownloadError(
        f"Download failed: {detail}",
        reason="download_failed",
        hint="Try again, update yt-dlp, or upload the video file instead.",
    )


def _detail(text: str) -> str:
    """The last ERROR line, trimmed: that is the only line that matters."""
    for line in reversed(text.splitlines()):
        if "ERROR" in line:
            return re.sub(r"^ERROR:\s*", "", line).strip()[:300]
    return ""


# --------------------------------------------------------------- metadata --

INFO_FIELDS = [
    "id", "title", "channel", "channel_id", "uploader", "duration", "is_live",
    "was_live", "live_status", "thumbnails", "chapters", "heatmap", "upload_date",
    "view_count", "channel_url", "webpage_url", "subtitles", "automatic_captions",
    "availability", "age_limit", "description",
]


def metadata(url: str, timeout: int = 60) -> dict:
    """Fetch metadata only. This never downloads video bytes."""
    # --dump-single-json returns every field we need in one pass, with no video
    # bytes downloaded.
    args = ["--dump-single-json", "--skip-download", "--no-check-formats", url]
    prefix = command()
    if not prefix:
        raise DownloadError(
            "yt-dlp is not installed, so links cannot be checked.",
            reason="no_ytdlp",
            hint="Install it with: pip install -r requirements.txt",
        )
    try:
        proc = subprocess.run(
            [*prefix, *base_args(), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise DownloadError(
            "YouTube did not answer in time.",
            reason="timeout",
            hint="Try again, or upload the file instead.",
        ) from exc
    if proc.returncode != 0:
        raise classify(proc.stdout + "\n" + proc.stderr)
    raw = proc.stdout.strip()
    # --dump-single-json may be preceded by warnings on stdout
    start = raw.find("{")
    if start > 0:
        raw = raw[start:]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DownloadError(
            "YouTube returned something unreadable.",
            reason="bad_json",
            hint="Update yt-dlp, then retry.",
        ) from exc
    return normalise_info(data)


def normalise_info(data: dict) -> dict:
    """One stable shape for the rest of the app, whatever yt-dlp returns."""
    live_status = data.get("live_status") or ("is_live" if data.get("is_live") else "not_live")
    if live_status in ("post_live", "was_live"):
        state = "ended_live"
    elif live_status == "is_live":
        state = "live"
    elif live_status in ("is_upcoming", "is_premiere"):
        state = "upcoming"
    else:
        state = "video"

    thumbs = [t for t in (data.get("thumbnails") or []) if t.get("url")]
    thumbs.sort(key=lambda t: (t.get("width") or 0) * (t.get("height") or 0), reverse=True)

    heatmap = []
    for seg in data.get("heatmap") or []:
        try:
            start = float(seg.get("start_time"))
            end = float(seg.get("end_time"))
            value = float(seg.get("value"))
        except (TypeError, ValueError):
            continue
        heatmap.append({"start": start, "end": end, "value": value})

    chapters = []
    for ch in data.get("chapters") or []:
        try:
            chapters.append({
                "title": str(ch.get("title") or "")[:200],
                "start": float(ch.get("start_time") or 0),
                "end": float(ch.get("end_time") or 0),
            })
        except (TypeError, ValueError):
            continue

    subs = data.get("subtitles") or {}
    autos = data.get("automatic_captions") or {}

    return {
        "video_id": data.get("id") or "",
        "title": data.get("title") or "Untitled",
        "channel": data.get("channel") or data.get("uploader") or "",
        "channel_id": data.get("channel_id") or "",
        "duration": float(data.get("duration") or 0),
        "state": state,
        "live_status": live_status,
        "thumbnails": [t["url"] for t in thumbs[:8]],
        "thumbnail": thumbs[0]["url"] if thumbs else None,
        "chapters": chapters,
        "heatmap": heatmap,
        "upload_date": data.get("upload_date") or "",
        "view_count": data.get("view_count"),
        "description": (data.get("description") or "")[:4000],
        "age_limit": data.get("age_limit") or 0,
        "availability": data.get("availability") or "public",
        "has_manual_subs": bool(subs),
        "has_auto_subs": bool(autos),
        "sub_langs": sorted(set(list(subs.keys()) + list(autos.keys())))[:20],
        "url": data.get("webpage_url") or "",
    }


# ------------------------------------------------------------- downloads --

VIDEO_FORMAT = (
    "bv*[height<=1080][ext=mp4][vcodec^=avc1]+ba[ext=m4a]/"
    "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*+ba/b"
)


def download_audio(url: str, out: Path, on_progress: Progress | None = None) -> Path:
    """Audio only, mono 16 kHz: enough for loudness maths and Whisper."""
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            url,
            "-f", "bestaudio/best",
            "--extract-audio",
            "--audio-format", "wav",
            "--postprocessor-args", "-ac 1 -ar 16000",
            "-o", str(out.with_suffix("")) + ".%(ext)s",
            "--retries", "2",
        ],
        on_progress,
        label="Downloading audio",
        tries=2,
    )
    for candidate in (out, out.with_suffix(".wav")):
        if candidate.exists():
            return candidate
    found = sorted(out.parent.glob(out.stem + "*"), key=lambda p: p.stat().st_size, reverse=True)
    if not found:
        raise DownloadError("Audio download finished but no file appeared.", reason="no_audio")
    return found[0]


def download_segment(url: str, start: float, end: float, out: Path,
                     on_progress: Progress | None = None) -> Path:
    """Download only [start, end]. This is what keeps long sources cheap."""
    out.parent.mkdir(parents=True, exist_ok=True)
    pad_start = max(0.0, start - 0.5)
    pad_end = end + 0.5
    _run(
        [
            url,
            "-f", VIDEO_FORMAT,
            "--download-sections", f"*{pad_start:.2f}-{pad_end:.2f}",
            "--force-keyframes-at-cuts",
            "--merge-output-format", "mp4",
            "-o", str(out),
            "--retries", "2",
        ],
        on_progress,
        duration=pad_end - pad_start,
        label="Downloading the clip",
        tries=2,
    )
    if out.exists() and out.stat().st_size > 1024:
        return out
    found = sorted(out.parent.glob(out.stem + "*.mp4"), key=lambda p: p.stat().st_size, reverse=True)
    if not found:
        raise DownloadError(
            "Only the clip section should be downloaded, but no file appeared.",
            reason="no_segment",
            hint="Your yt-dlp may be too old for --download-sections. Update it.",
        )
    return found[0]


def download_subs(url: str, langs: str, out_dir: Path,
                  auto: bool = True) -> dict[str, Path]:
    """Subtitle files by language code. Includes live chat when asked (`live_chat`)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    args = [
        url,
        "--skip-download",
        "--write-subs" if not auto else "--write-auto-subs",
        "--sub-langs", langs,
        "--sub-format", "json3/vtt/best",
        "--convert-subs", "vtt",
        "-o", str(out_dir / "%(id)s.%(ext)s"),
        "--retries", "2",
    ]
    try:
        _run(args, tries=1, label="Fetching subtitles")
    except DownloadError:
        return {}
    found: dict[str, Path] = {}
    for path in sorted(out_dir.glob("*.vtt")):
        code = path.name.split(".")[-2] if path.name.count(".") >= 2 else "unknown"
        found[code] = path
    return found


def list_chat(url: str, out_dir: Path) -> Path | None:
    """Live chat replay for a finished stream, as vtt. None when unavailable."""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        _run(
            [
                url,
                "--skip-download",
                "--write-subs",
                "--sub-langs", "live_chat",
                "--sub-format", "json3",
                "-o", str(out_dir / "chat.%(ext)s"),
                "--retries", "1",
            ],
            tries=1,
            label="Fetching chat replay",
        )
    except DownloadError:
        return None
    for path in sorted(out_dir.glob("chat*.json3*"), key=lambda p: p.stat().st_size, reverse=True):
        if path.stat().st_size > 0:
            return path
    return None


def record_live(url: str, out: Path, cap_seconds: int = 1800,
                on_progress: Progress | None = None) -> Path:
    """Record a live stream from now, capped.

    yt-dlp's own --live-from-start needs a DVR-enabled stream; when that is not
    possible we still capture what airs while the job runs, which is what
    "analyze what has been streamed so far" needs.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [
        url,
        "-f", VIDEO_FORMAT,
        "--live-from-start",
        "--merge-output-format", "mp4",
        "-o", str(out),
    ]
    prefix = command()
    if not prefix:
        raise DownloadError("yt-dlp is not installed.", reason="no_ytdlp")
    cmd = [*prefix, *base_args(), *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    started = time.time()
    assert proc.stdout is not None
    tail: list[str] = []
    for raw in proc.stdout:
        line = raw.strip()
        if line:
            tail.append(line)
            tail = tail[-20:]
        elapsed = time.time() - started
        if on_progress:
            on_progress(min(0.99, elapsed / max(1, cap_seconds)), f"Recording live… {elapsed:.0f}s")
        if elapsed >= cap_seconds:
            proc.terminate()
            break
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    if out.exists() and out.stat().st_size > 1024:
        return out
    found = sorted(out.parent.glob(out.stem + "*.mp4"), key=lambda p: p.stat().st_size, reverse=True)
    if found:
        return found[0]
    raise classify(tail)
