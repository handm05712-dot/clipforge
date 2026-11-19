"""Transcript text helpers: sentence spans, fillers, silence removal plans.

Everything here works on the word list produced by transcribe.py:
    [{"text": str, "start": float, "end": float}, ...]
"""
from __future__ import annotations

import re

FILLERS = {
    "um", "umm", "uh", "uhh", "erm", "er", "ah", "ahh", "hmm", "mm", "mmm",
    "uhm", "eh", "like,", "you know,", "i mean,", "sort of,", "kind of,",
}
TERMINAL = (".", "!", "?", "…")
SOFT = (",", ";", ":", "—", "-")

EMOJI_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(money|cash|dollar|rich|profit|revenue|sales|paid)\b", re.I), "💰"),
    (re.compile(r"\b(love|heart|amazing|beautiful)\b", re.I), "❤️"),
    (re.compile(r"\b(fast|quick|speed|instant)\b", re.I), "⚡"),
    (re.compile(r"\b(idea|think|brain|smart|learn)\b", re.I), "🧠"),
    (re.compile(r"\b(secret|hidden|nobody|truth)\b", re.I), "🤫"),
    (re.compile(r"\b(crazy|insane|wild|shocking)\b", re.I), "🤯"),
    (re.compile(r"\b(grow|growth|up|increase|scale)\b", re.I), "📈"),
    (re.compile(r"\b(mistake|wrong|fail|stop|warn)\b", re.I), "⚠️"),
    (re.compile(r"\b(work|job|career|business)\b", re.I), "💼"),
    (re.compile(r"\b(fire|hot|viral|trend)\b", re.I), "🔥"),
    (re.compile(r"\b(time|today|now|never)\b", re.I), "⏰"),
    (re.compile(r"\b(start|begin|first|step)\b", re.I), "🚀"),
]

_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def clean_word(text: str) -> str:
    return text.strip()


def is_filler(text: str) -> bool:
    core = re.sub(r"[^\w']", "", text.lower())
    return core in {f.strip(",'") for f in FILLERS}


def sentence_spans(words: list[dict], min_gap: float = 0.6) -> list[dict]:
    """Group words into sentence-ish units that never split a word."""
    spans: list[dict] = []
    cur: list[dict] = []
    for i, w in enumerate(words):
        if not cur:
            cur.append(w)
            continue
        prev = cur[-1]
        gap = w["start"] - prev["end"]
        starts_new = prev["text"].endswith(TERMINAL) or gap >= min_gap
        if starts_new:
            spans.append(_span(cur))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        spans.append(_span(cur))
    # Merge very short spans into the previous one so clips read naturally,
    # but never grow a span past ~15s (that would swallow whole paragraphs).
    merged: list[dict] = []
    for sp in spans:
        too_short = len(sp["words"]) < 6 and (sp["end"] - sp["start"]) < 3.0
        if merged and too_short and (sp["end"] - merged[-1]["start"]) <= 15.0:
            merged[-1] = _span(merged[-1]["words"] + sp["words"])
        else:
            merged.append(sp)
    return merged


def _span(words: list[dict]) -> dict:
    return {
        "start": round(words[0]["start"], 3),
        "end": round(words[-1]["end"], 3),
        "text": " ".join(w["text"] for w in words).strip(),
        "words": words,
    }


def join_words(words: list[dict]) -> str:
    out = ""
    for w in words:
        t = w["text"]
        if not out:
            out = t
        elif t[:1] in ",.!?;:%)\"'’":
            out += t
        else:
            out += " " + t
    return out.strip()


def words_between(words: list[dict], start: float, end: float) -> list[dict]:
    return [w for w in words if w["end"] > start + 1e-3 and w["start"] < end - 1e-3]


def snap_boundaries(words: list[dict], start: float, end: float,
                    tolerance: float = 0.35) -> tuple[float, float]:
    """Nudge clip bounds onto word edges so cuts never slice a syllable."""
    if not words:
        return start, end
    s = min(words, key=lambda w: abs(w["start"] - start))
    e = min(words, key=lambda w: abs(w["end"] - end))
    if abs(s["start"] - start) <= tolerance or start <= words[0]["start"]:
        start = s["start"]
    if abs(e["end"] - end) <= tolerance or end >= words[-1]["end"]:
        end = e["end"]
    if end - start < 1.0:
        end = max(end, start + 1.0)
    return round(start, 3), round(end, 3)


def silence_ranges(words: list[dict], duration: float, min_gap: float = 0.55,
                   head_pad: float = 0.12, tail_pad: float = 0.08) -> list[list[float]]:
    """Return [[gap_start, gap_end], ...] regions with no speech."""
    ranges: list[list[float]] = []
    cursor = 0.0
    for w in words:
        if w["start"] - cursor >= min_gap:
            ranges.append([cursor, w["start"]])
        cursor = max(cursor, w["end"])
    if duration - cursor >= min_gap:
        ranges.append([cursor, duration])
    out = []
    for a, b in ranges:
        a2, b2 = a + head_pad, b - tail_pad
        if b2 - a2 > 0.08:
            out.append([round(a2, 3), round(b2, 3)])
    return out


def keep_ranges(words: list[dict], duration: float, drop_fillers: bool = True,
                trim_silence: bool = True, min_gap: float = 0.55,
                pad: float = 0.05) -> list[list[float]]:
    """Regions of the audio to keep after removing fillers and long pauses.

    Filler words are cut out of the middle of sentences, so a sentence can
    produce several kept ranges. Ranges are merged when the gap left behind is
    too small to be worth a cut (avoiding audible choppiness).
    """
    duration = max(0.0, float(duration))
    kept = [w for w in words if not (drop_fillers and is_filler(w["text"]))]
    if not kept:
        return [[0.0, duration]] if duration > 0 else []
    if not trim_silence:
        return [[0.0, duration]]

    ranges: list[list[float]] = []
    for w in kept:
        a = max(0.0, float(w["start"]) - pad)
        b = min(duration, float(w["end"]) + pad)
        if b <= a:
            continue
        if ranges and a - ranges[-1][1] <= 0.08:
            # words touch after padding: same continuous phrase
            ranges[-1][1] = max(ranges[-1][1], b)
        else:
            ranges.append([a, b])
    out = [[round(a, 3), round(b, 3)] for a, b in ranges if b - a > 0.12]
    return out or [[0.0, duration]]


def map_kept_range(words: list[dict], keep: list[list[float]]) -> list[dict]:
    """Rewrite word timestamps onto the compressed (edited) timeline.

    Each word belongs to exactly one kept range: the one containing its midpoint.
    Words that fall entirely inside a cut are dropped.
    """
    out: list[dict] = []
    for w in words:
        mid = (float(w["start"]) + float(w["end"])) / 2
        offset = 0.0
        placed = False
        for a, b in keep:
            if a <= mid <= b:
                ws = max(float(w["start"]), a) - a + offset
                we = min(float(w["end"]), b) - a + offset
                if we - ws < 0.02:
                    we = ws + 0.04
                out.append({**w, "start": round(ws, 3), "end": round(we, 3)})
                placed = True
                break
            offset += b - a
        if not placed:
            continue
    out.sort(key=lambda x: x["start"])
    return out


def auto_emoji(text: str, words: list[dict], every: int = 14) -> list[dict]:
    """Attach an emoji to a word occasionally based on keywords."""
    injected: list[dict] = []
    last = -every
    for i, w in enumerate(words):
        item = dict(w)
        if i - last >= every:
            for pattern, emoji in EMOJI_HINTS:
                if pattern.search(w["text"]):
                    item["emoji"] = emoji
                    last = i
                    break
        injected.append(item)
    return injected


def chunk_words(words: list[dict], per_line: int, max_chars: int = 26) -> list[list[dict]]:
    """Group words into caption lines of 1-3 words."""
    lines: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        cur.append(w)
        chars = len(join_words(cur))
        if len(cur) >= per_line or chars >= max_chars:
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    return lines
