"""Stage A — cheap signals, before any video is downloaded.

Everything here is a pure function over data: a heatmap list, a chat log, an
audio envelope, a word list. That keeps it unit-testable and means a missing
signal is a normal branch rather than an exception.

Every extractor returns None when the signal is absent, so the scorer knows the
difference between "measured, found nothing" and "could not measure".
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

# ------------------------------------------------------------------ heatmap --


def heatmap_curve(segments: list[dict], duration: float, bucket: float = 1.0) -> list[float] | None:
    """Resample YouTube's uneven heatmap segments onto a 1 s grid, 0..1.

    Segments are already normalised per video, but they have wildly different
    widths, so resampling first is what makes "peak" mean the same thing twice.
    """
    if not segments:
        return None
    n = max(1, int(math.ceil(duration / bucket)))
    curve = [0.0] * n
    for seg in segments:
        try:
            start = float(seg["start"])
            end = float(seg["end"])
            value = float(seg["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        first = max(0, int(start // bucket))
        last = min(n - 1, int(end // bucket))
        for i in range(first, last + 1):
            # weight by how much of this bucket the segment actually covers
            lo = max(start, i * bucket)
            hi = min(end, (i + 1) * bucket)
            overlap = max(0.0, hi - lo) / bucket
            curve[i] = max(curve[i], value * overlap if overlap < 1 else value)
    peak = max(curve) or 1.0
    return [round(v / peak, 4) for v in curve]


def heatmap_peaks(segments: list[dict], duration: float,
                  min_sep: float = 20.0, limit: int = 12) -> list[dict]:
    """Local maxima of the heatmap, spaced out, strongest first."""
    curve = heatmap_curve(segments, duration)
    if not curve:
        return []
    return [{"at": p["at"], "strength": p["strength"], "source": "heatmap"}
            for p in _peaks(curve, 1.0, min_sep, limit)]


# --------------------------------------------------------------------- chat --

HYPE = ["lmao", "lol", "w", "kekw", "omg", "no way", "holy", "wtf", "pog",
        "clip it", "clip that", "insane", "let's go", "lets go", "gg"]
HYPE_EMOJI = ["😂", "🤣", "🔥", "💀", "😭", "👏"]

_VTT_TIME = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})\.(\d{3})"
)


def parse_chat(path: Path) -> list[dict]:
    """Read a live-chat replay into [{at, text}].

    Two shapes exist: yt-dlp's JSON3 (preferred, exact times) and the WebVTT
    download. Chat replay from YouTube is usually partial — "top chat" only —
    so callers treat this as a bonus signal.
    """
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".json3" or raw.lstrip().startswith("{"):
        return _parse_chat_json3(raw)
    return _parse_chat_vtt(raw)


def _parse_chat_json3(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out = []
    for event in data.get("events") or []:
        segs = event.get("segs") or []
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text:
            continue
        at = (event.get("tStartMs") or 0) / 1000
        out.append({"at": float(at), "text": text})
    return out


def _parse_chat_vtt(raw: str) -> list[dict]:
    """Cues look like `<00:12:31.500><c>username</c>: message`."""
    out = []
    current = None
    for line in raw.splitlines():
        m = _VTT_TIME.match(line.strip())
        if m:
            h, mi, s, _ = (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4))
            current = h * 3600 + mi * 60 + s
            continue
        if current is None or not line.strip() or line.startswith(("WEBVTT", "Kind:", "Language:")):
            continue
        text = re.sub(r"<[^>]+>", "", line)
        text = re.sub(r"^[^:]{1,40}:\s*", "", text)  # drop the username prefix
        text = text.strip()
        if text:
            out.append({"at": float(current), "text": text})
    return out


def chat_spikes(messages: list[dict], duration: float, bucket: float = 5.0,
                min_sep: float = 20.0, limit: int = 10) -> list[dict]:
    """Messages per bucket, z-scored against a rolling median.

    A rolling median rather than a global mean because a stream's chatter grows
    steadily; the interesting moment is a spike *relative to the last minute*,
    not relative to the whole stream.
    """
    if not messages or duration <= 0:
        return []
    n = max(1, int(math.ceil(duration / bucket)))
    counts = [0] * n
    hype = [0] * n
    half = max(1, int(30 / bucket))
    for msg in messages:
        at = float(msg.get("at") or 0)
        if at < 0 or at >= duration:
            continue
        i = min(n - 1, int(at // bucket))
        counts[i] += 1
        low = (msg.get("text") or "").lower()
        if any(tok in low for tok in HYPE) or any(e in (msg.get("text") or "") for e in HYPE_EMOJI):
            hype[i] += 1
    if not any(counts):
        return []
    window = [max(0, i - half) for i in range(n)]
    scores = []
    for i in range(n):
        lo = window[i]
        hi = min(n, i + half + 1)
        neigh = sorted(counts[lo:hi]) or [0]
        median = neigh[len(neigh) // 2]
        mad = sorted(abs(c - median) for c in neigh)[len(neigh) // 2] or 1.0
        z = (counts[i] - median) / (1.4826 * mad)
        if counts[i] >= 3 and z > 2.0:
            scores.append({"at": i * bucket, "z": z, "strength": z,
                           "hype": hype[i], "source": "chat"})
    scores.sort(key=lambda p: -p["strength"])
    return _space_out(scores, min_sep)[:limit]


# -------------------------------------------------------------------- audio --

def audio_envelope(path: Path, bucket: float = 1.0) -> list[float] | None:
    """Per-bucket loudness in dBFS, via ffmpeg's astats.

    astats prints one block per audio frame; we ask for 1 s buckets with
    `asetnsamples` so the numbers line up with the rest of the timeline.
    """
    from . import media

    if not path.exists():
        return None
    try:
        out = media.run_capture([
            "-i", str(path),
            "-af", f"asetnsamples=n={int(16000 * bucket)},astats=metadata=1:reset=1,"
                   "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
            "-f", "null", "-",
        ])
    except Exception:
        return None
    values = [float(m) for m in re.findall(r"RMS_level=(-?\d+(?:\.\d+)?)", out or "")]
    # -inf shows up as -inf; clamp to silence
    values = [max(-90.0, v if v > -90 else -90.0) for v in values]
    return values or None


def audio_spikes(envelope: list[float] | None, bucket: float = 1.0,
                 min_sep: float = 20.0, limit: int = 10) -> list[dict]:
    """Sudden loudness jumps: shouting, laughter, a hit, a crowd.

    Compares each second to the median of the surrounding 20 s, so a loud
    recording does not read as a spike everywhere.
    """
    if not envelope or len(envelope) < 5:
        return []
    half = 10
    peaks = []
    for i, db in enumerate(envelope):
        lo = max(0, i - half)
        hi = min(len(envelope), i + half + 1)
        neigh = sorted(envelope[lo:hi])
        median = neigh[len(neigh) // 2]
        rise = db - median
        if rise >= 6.0:  # ~2x perceived loudness
            peaks.append({"at": i * bucket, "strength": rise, "db": db, "source": "audio"})
    peaks.sort(key=lambda p: -p["strength"])
    return _space_out(peaks, min_sep)[:limit]


# ------------------------------------------------------------------- shared --


def _peaks(curve: list[float], bucket: float, min_sep: float, limit: int) -> list[dict]:
    out = []
    for i in range(1, len(curve) - 1):
        if curve[i] >= curve[i - 1] and curve[i] > curve[i + 1] and curve[i] > 0.35:
            out.append({"at": i * bucket, "strength": round(curve[i], 4)})
    if not out and curve:
        # a flat heatmap has no local maxima; take the top of the curve
        best = max(range(len(curve)), key=lambda i: curve[i])
        out = [{"at": best * bucket, "strength": round(curve[best], 4)}]
    out.sort(key=lambda p: -p["strength"])
    return _space_out(out, min_sep)[:limit]


def _space_out(peaks: list[dict], min_sep: float) -> list[dict]:
    """Keep the strongest peak when two land within min_sep seconds."""
    kept: list[dict] = []
    for peak in sorted(peaks, key=lambda p: -p["strength"]):
        if all(abs(peak["at"] - k["at"]) >= min_sep for k in kept):
            kept.append(peak)
    return kept


def summarise(signals: dict) -> list[str]:
    """Human labels for the results UI: which signals actually carried weight."""
    labels = []
    if signals.get("heatmap"):
        labels.append("Most replayed")
    if signals.get("chat"):
        labels.append("Chat spike")
    if signals.get("audio"):
        labels.append("Loud moment")
    if signals.get("transcript_source") == "youtube":
        labels.append("Creator captions")
    elif signals.get("transcript_source") == "whisper":
        labels.append("Whisper transcript")
    return labels
