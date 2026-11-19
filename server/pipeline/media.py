"""FFmpeg / ffprobe helpers shared by the pipeline."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable

from .. import config

Progress = Callable[[float], None]


class MediaError(RuntimeError):
    pass


def ffmpeg_version() -> str:
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=20)
        return out.stdout.splitlines()[0] if out.stdout else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "missing"


def probe(path: Path | str) -> dict:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise MediaError(f"ffprobe failed: {res.stderr[-400:]}")
    info = json.loads(res.stdout or "{}")
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not v:
        raise MediaError("The file has no video stream.")
    width, height = int(v.get("width") or 0), int(v.get("height") or 0)
    rotation = 0
    for side in v.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = int(abs(int(side["rotation"])))
    if rotation in (90, 270):
        width, height = height, width
    fps = 30.0
    m = re.match(r"(\d+)/(\d+)", v.get("avg_frame_rate") or v.get("r_frame_rate") or "")
    if m and int(m.group(2)):
        fps = int(m.group(1)) / int(m.group(2))
    duration = float(info.get("format", {}).get("duration") or v.get("duration") or 0.0)
    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "has_audio": bool(a),
        "video_codec": v.get("codec_name"),
        "size": int(info.get("format", {}).get("size") or 0),
    }


def run(args: list[str], duration: float | None = None,
        on_progress: Progress | None = None, cwd: Path | None = None) -> str:
    """Run ffmpeg with -progress parsing. Returns stderr tail."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-progress", "pipe:1", *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=str(cwd) if cwd else None)
    stderr_tail = ""
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if on_progress and duration and "=" in line:
            key, _, val = line.partition("=")
            if key in ("out_time_us", "out_time_ms"):
                try:
                    us = float(val)
                    # out_time_ms is actually microseconds in ffmpeg >= 4
                    secs = us / 1_000_000
                    on_progress(min(0.99, max(0.0, secs / duration)))
                except ValueError:
                    pass
    if proc.stderr is not None:
        stderr_tail = proc.stderr.read()[-3000:]
    code = proc.wait()
    if code != 0:
        raise MediaError(f"ffmpeg exited {code}: {stderr_tail}")
    return stderr_tail


def run_capture(args: list[str], timeout: int = 300) -> str:
    """Run ffmpeg and return its combined output (for metadata filters)."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info", *args]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (res.stdout or "") + (res.stderr or "")


def extract_audio(src: Path, out: Path, on_progress: Progress | None = None,
                  duration: float | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(out)], duration=duration, on_progress=on_progress)
    return out


def thumbnail(src: Path, at: float, out: Path, width: int = 480) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["-y", "-ss", f"{max(0.0, at):.3f}", "-i", str(src), "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "4", str(out)])
    return out


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def even(n: float) -> int:
    return max(2, int(n) // 2 * 2)


def filter_escape_path(path: Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    p = str(path.resolve())
    p = p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return p
