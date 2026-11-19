"""Stages B–D: candidates, boundaries, scoring and selection.

The maths lives here so it can be tested without YouTube, ffmpeg or an LLM.
`viral/finder.py` is the orchestrator that feeds these functions real data.
"""
from __future__ import annotations

import re
from pathlib import Path

try:  # weights are tunable without touching code
    import yaml
except Exception:  # pragma: no cover - PyYAML is optional
    yaml = None

CONFIG_PATH = Path(__file__).with_name("weights.yaml")

DEFAULT_WEIGHTS = {
    "llm": 0.50,
    "heatmap": 0.20,
    "chat": 0.15,
    "audio": 0.15,
}

DEFAULT_RUBRIC = {
    "hook": 0.18,
    "emotion": 0.14,
    "payoff": 0.18,
    "standalone": 0.14,
    "shareable": 0.12,
    "novelty": 0.10,
    "pacing": 0.09,
    "relevance": 0.05,
}


def load_config() -> dict:
    """Signal + rubric weights, overridable from weights.yaml."""
    cfg = {"signals": dict(DEFAULT_WEIGHTS), "rubric": dict(DEFAULT_RUBRIC)}
    if yaml is not None and CONFIG_PATH.exists():
        try:
            loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
            for key in ("signals", "rubric"):
                if isinstance(loaded.get(key), dict):
                    cfg[key] = {**cfg[key], **{k: float(v) for k, v in loaded[key].items()}}
        except Exception:
            pass
    return cfg


# ------------------------------------------------------------- candidates --


def from_peaks(peaks: list[dict], target: float, min_len: float, max_len: float,
               total: float) -> list[dict]:
    """Turn a signal peak into a candidate window centred on the moment."""
    out = []
    for peak in peaks:
        at = float(peak.get("at") or 0)
        # a peak marks the hook, so start slightly before it
        start = max(0.0, at - target * 0.15)
        end = min(total, start + target)
        if end - start < min_len:
            start = max(0.0, end - min_len)
        out.append({
            "start": start, "end": end,
            "origin": peak.get("source", "signal"),
            "signal_strength": float(peak.get("strength") or 0),
        })
    return out


def merge(candidates: list[dict], overlap_ratio: float = 0.5) -> list[dict]:
    """Merge candidates that overlap by more than `overlap_ratio`.

    Keeps the union of both spans and remembers every origin, so a moment
    flagged by heatmap *and* chat outranks one flagged by either alone.
    """
    ordered = sorted(candidates, key=lambda c: (c["start"], c["end"]))
    merged: list[dict] = []
    for cand in ordered:
        placed = False
        for existing in merged:
            inter = min(existing["end"], cand["end"]) - max(existing["start"], cand["start"])
            shorter = min(existing["end"] - existing["start"], cand["end"] - cand["start"])
            if shorter > 0 and inter / shorter > overlap_ratio:
                existing["start"] = min(existing["start"], cand["start"])
                existing["end"] = max(existing["end"], cand["end"])
                existing["origins"] = sorted(set(existing.get("origins", []) + [cand.get("origin")]))
                existing["signal_strength"] = max(
                    existing.get("signal_strength", 0), cand.get("signal_strength", 0)
                )
                placed = True
                break
        if not placed:
            merged.append({**cand, "origins": [cand.get("origin", "signal")]})
    return merged


# -------------------------------------------------------------- boundaries --

FILLER_START = re.compile(
    r"^\s*(um+|uh+|er+|ah+|so|okay|ok|yeah|right|well|like|you know)\b[\s,.]*", re.I
)


def snap(candidate: dict, words: list[dict], min_len: float, max_len: float,
         total: float, lead_in: float = 0.8) -> dict:
    """Start on an utterance and end after the payoff.

    Rules, in order:
      1. Start at the beginning of the sentence containing the hook, or just
         before it, never in the middle of a word.
      2. Skip filler words at the start ("um, so…") if a clean start exists
         within a second or two.
      3. Never end mid-word or mid-sentence; end after the last full sentence.
      4. Give up to 10 s of extra setup if the moment needs it, and trim to the
         strongest stretch if it runs past max_len.
    """
    if not words:
        return {**candidate, "snapped": False, "start": max(0.0, candidate["start"]),
                "end": min(total, candidate["end"])}

    start = float(candidate["start"])
    end = float(candidate["end"])

    # 1. the word we would cut into
    idx = _index_at(words, start)
    # 2. walk back to a sentence start for the setup, at most 10 s
    begin = _sentence_start(words, idx, floor=max(0.0, start - 10.0))
    start = max(0.0, words[begin]["start"] - lead_in)
    if start < 0:
        start = 0.0

    # 3. skip filler at the start when there is something clean right after
    begin = _skip_filler(words, begin, start, end)

    # 4. end on a sentence boundary, just after the payoff
    end_idx = max(begin, _index_at(words, end))
    close_idx = _sentence_end(words, end_idx)
    end = min(total, words[close_idx]["end"] + 0.5)

    if end - start > max_len:
        # too long: keep the strongest run, still starting where we start
        end = start + max_len
        end = min(total, words[_sentence_end(words, _index_at(words, end))]["end"] + 0.5)
    if end - start < min_len:
        # Too short: grow to the next sentence end instead of padding with
        # silence, then only fall back to an exact trim if there is no speech.
        grown = _sentence_end(words, _index_at(words, start + min_len))
        end = min(total, max(end, words[grown]["end"] + 0.5))
        if end - start < min_len:
            end = min(total, start + min_len)
    if end - start < min_len and total >= min_len:
        start = max(0.0, end - min_len)

    # the first printable word decides whether we cut a word in half
    seg = [w for w in words if w["end"] > start and w["start"] < end]

    return {
        **candidate,
        "start": round(start, 3),
        "end": round(end, 3),
        "snapped": True,
        "text": " ".join(w.get("text", "") for w in seg)[:4000],
        "words": seg,
    }


def _index_at(words: list[dict], t: float) -> int:
    for i, w in enumerate(words):
        if w["end"] > t:
            return i
    return len(words) - 1


def _sentence_start(words: list[dict], idx: int, floor: float) -> int:
    """Walk back to a sentence boundary, or to `floor` if none is nearby."""
    i = max(0, min(idx, len(words) - 1))
    while i > 0:
        prev = words[i - 1].get("text", "")
        if re.search(r"[.!?…]['\"”’)]?$", prev.strip()) or words[i]["start"] - words[i - 1]["end"] > 0.6:
            return i
        if words[i]["start"] < floor:
            return i
        i -= 1
    return 0


def _sentence_end(words: list[dict], idx: int) -> int:
    i = max(0, min(idx, len(words) - 1))
    while i < len(words) - 1:
        if re.search(r"[.!?…]['\"”’)]?$", words[i].get("text", "").strip()):
            return i
        i += 1
    return len(words) - 1


def _skip_filler(words: list[dict], begin: int, start: float, end: float) -> int:
    """Drop leading fillers only when a better start is close by."""
    i = begin
    while i < len(words) - 1 and FILLER_START.match(words[i].get("text", "")):
        if words[i + 1]["start"] - start > 2.5:
            return i
        i += 1
        start = words[i]["start"]
    return i


# ------------------------------------------------------------------ scoring --


def llm_score(candidate: dict, cfg: dict | None = None) -> float:
    """Weighted 0–100 from the judge's per-dimension scores."""
    cfg = cfg or load_config()
    rubric = cfg["rubric"]
    scores = candidate.get("scores") or {}
    total = 0.0
    weight = 0.0
    for key, w in rubric.items():
        value = scores.get(key)
        if value is None:
            continue
        total += max(0.0, min(100.0, float(value))) * w
        weight += w
    if not weight:
        return float(candidate.get("total") or 0)
    return total / weight


def blend(candidate: dict, cfg: dict | None = None) -> dict:
    """Final score from LLM + whichever signals are actually present.

    Missing signals are dropped and the remaining weights renormalised, so a
    video without a heatmap is not punished, it is judged on what exists.
    """
    cfg = cfg or load_config()
    weights = cfg["signals"]
    parts: dict[str, float] = {}

    if candidate.get("scores"):
        parts["llm"] = llm_score(candidate, cfg)
    for key in ("heatmap", "chat", "audio"):
        value = (candidate.get("signals") or {}).get(key)
        if value is None:
            continue
        parts[key] = max(0.0, min(100.0, float(value) * 100))

    if not parts:
        return {"total": 0.0, "used": [], "parts": {}}

    used = {k: weights.get(k, 0.0) for k in parts}
    denom = sum(used.values()) or 1.0
    total = sum(parts[k] * (used[k] / denom) for k in parts)
    return {"total": round(total, 1), "used": sorted(parts), "parts": {k: round(v, 1) for k, v in parts.items()}}


def select(candidates: list[dict], count: int, min_gap: float = 30.0) -> list[dict]:
    """Top `count` non-overlapping candidates, at least `min_gap` apart.

    Variety rule: prefer the best candidate that is far from everything already
    chosen, so one great stretch does not produce five near-copies.
    """
    ranked = sorted(candidates, key=lambda c: -c.get("total", 0))
    chosen: list[dict] = []
    for cand in ranked:
        if len(chosen) >= count:
            break
        if any(_too_close(cand, c, min_gap) for c in chosen):
            continue
        chosen.append(cand)
    return chosen


def _too_close(a: dict, b: dict, min_gap: float) -> bool:
    if abs(a["start"] - b["start"]) < min_gap:
        return True
    inter = min(a["end"], b["end"]) - max(a["start"], b["start"])
    return inter > 0


# ---------------------------------------------------------------- metadata --

HASHTAG_BAD = re.compile(r"[^a-z0-9]")


def tidy_title(title: str, limit: int = 60) -> str:
    text = re.sub(r"\s+", " ", (title or "").strip())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,.;:-")


def tidy_hashtags(tags: list[str] | None, fallback: list[str] | None = None) -> list[str]:
    out: list[str] = []
    for tag in (tags or []) + (fallback or []):
        clean = HASHTAG_BAD.sub("", str(tag).strip().lower().replace(" ", ""))
        if not clean or len(clean) > 30:
            continue
        if clean not in [t.lstrip("#") for t in out]:
            out.append("#" + clean)
        if len(out) >= 8:
            break
    return out
