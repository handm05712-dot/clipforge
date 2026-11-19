"""Audio helpers: silence/filler removal plans and music auto-ducking."""
from __future__ import annotations

from . import text as txt


def build_keep_ranges(words: list[dict], duration: float,
                      remove_silences: bool, remove_fillers: bool) -> list[list[float]]:
    if not remove_silences and not remove_fillers:
        return [[0.0, round(duration, 3)]]
    ranges = txt.keep_ranges(words, duration, drop_fillers=remove_fillers,
                             trim_silence=remove_silences)
    if not ranges:
        return [[0.0, round(duration, 3)]]
    return ranges


def remap_words(words: list[dict], keep: list[list[float]]) -> list[dict]:
    """Translate word times into the compressed timeline used by the render."""
    if len(keep) == 1 and abs(keep[0][0]) < 1e-6:
        return [{**w, "start": round(w["start"] - keep[0][0], 3),
                 "end": round(w["end"] - keep[0][0], 3)} for w in words]
    return txt.map_kept_range(words, keep)


def speech_gate_filter() -> str:
    """VAD-ish gate hardcoded for the ducking sidechain input."""
    return "highpass=f=120,lowpass=f=8000"
