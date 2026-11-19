"""Clip detection: LLM ranking with a solid offline heuristic fallback."""
from __future__ import annotations

import json
import math
import re

import httpx

from . import text as txt

HYPE_WORDS = {
    "secret": 3, "nobody": 3, "mistake": 3, "never": 2, "always": 2, "best": 2,
    "worst": 2, "free": 2, "money": 3, "rich": 3, "million": 3, "thousand": 2,
    "why": 2, "how": 2, "stop": 2, "start": 1, "should": 2, "must": 2,
    "truth": 3, "lie": 2, "hate": 2, "love": 2, "crazy": 3, "insane": 3,
    "actually": 1, "really": 1, "important": 2, "easy": 2, "hard": 2,
    "fail": 2, "win": 2, "grow": 2, "fast": 2, "slow": 1, "people": 1,
    "you": 1, "your": 1, "i": 1, "we": 1, "because": 1, "but": 1,
    "viral": 4, "algorithm": 3, "hook": 3, "views": 2, "followers": 3,
    "business": 2, "client": 2, "price": 2, "sell": 2, "buy": 2, "learn": 2,
}


def _count_fillers(words: list[dict]) -> int:
    return sum(1 for w in words if txt.is_filler(w["text"]))


def _score_segment(words: list[dict], spans: list[dict]) -> float:
    if not words:
        return 0.0
    raw = txt.join_words(words).lower()
    tokens = re.findall(r"[a-z']+", raw)
    if not tokens:
        return 0.0
    hype = sum(HYPE_WORDS.get(t, 0) for t in tokens)
    hype = 20 * (1 - math.exp(-hype / 18))            # saturating 0..20
    dur = max(0.1, words[-1]["end"] - words[0]["start"])
    length_fit = 10 * math.exp(-((dur - 34) / 18) ** 2)  # peaks near 34s
    q_count = raw.count("?")
    exclaim = raw.count("!")
    energy = 8 * min(1.0, (q_count * 0.6 + exclaim * 0.4) / 2)
    density = 8 * min(1.0, len(tokens) / max(1.0, dur) / 3.2)
    first_person = 6 if re.search(r"\b(you|your|i|we)\b", raw) else 0
    filler_penalty = min(15, _count_fillers(words) * 2.0)
    starts_clean = 4 if words[0]["text"][:1].isupper() or words[0]["text"].endswith(txt.TERMINAL) else 2
    completes = 4 if words[-1]["text"].endswith(txt.TERMINAL) else 0
    score = 22 + hype + length_fit + energy + density + first_person + starts_clean + completes - filler_penalty
    return round(max(0.0, min(99.0, score + 12)), 1)


def heuristic_clips(words: list[dict], count: int = 7,
                    min_len: float = 20.0, max_len: float = 60.0) -> list[dict]:
    """Pick non-overlapping windows aligned to sentence boundaries."""
    if not words:
        return []
    spans = txt.sentence_spans(words)
    if not spans:
        return []
    candidates: list[dict] = []
    for i in range(len(spans)):
        for j in range(i, len(spans)):
            start, end = spans[i]["start"], spans[j]["end"]
            dur = end - start
            if dur < min_len * 0.75:
                continue
            if dur > max_len:
                break
            seg_words = [w for w in words if w["start"] >= start - 0.01 and w["end"] <= end + 0.01]
            if len(seg_words) < 12:
                continue
            sc = _score_segment(seg_words, spans)
            candidates.append({"start": start, "end": end, "score": sc,
                               "title": _title_from(seg_words), "words": seg_words})
    candidates.sort(key=lambda c: c["score"], reverse=True)
    picked: list[dict] = []
    for cand in candidates:
        if len(picked) >= count:
            break
        if any(cand["start"] < p["end"] - 1.0 and cand["end"] > p["start"] + 1.0 for p in picked):
            continue
        picked.append(cand)
    picked.sort(key=lambda c: c["start"])
    return picked


def _title_from(words: list[dict]) -> str:
    raw = txt.join_words(words)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return "Untitled clip"
    first = re.split(r"(?<=[.!?])\s", raw)[0]
    title = first if len(first) <= 70 else first[:67].rsplit(" ", 1)[0] + "…"
    return title.strip("\"' ")


def _prompt(transcript: str, count: int, target_hint: str) -> str:
    return f"""You are a short-form video editor. From the transcript below, pick the {count} most engaging segments for TikTok/Reels/Shorts.

Rules:
- Each segment must be {target_hint} long and start and end on whole sentences.
- Segments must not overlap.
- Give each a punchy hook title (max 8 words), a virality score 0-100, and a one-sentence reason.
- Return ONLY JSON: {{"clips":[{{"start": 12.4, "end": 48.2, "title": "...", "score": 87, "reason": "..."}}]}}
- start/end are seconds from the beginning of the transcript.

TRANSCRIPT:
{transcript}
"""


def _llm_clips(words: list[dict], count: int, settings: dict,
               min_len: float = 20, max_len: float = 60) -> list[dict] | None:
    key = settings.get("llm_api_key")
    if not key:
        return None
    spans = txt.sentence_spans(words)
    labeled = []
    for sp in spans:
        labeled.append(f"[{sp['start']:.1f}-{sp['end']:.1f}] {sp['text']}")
    transcript = "\n".join(labeled)[:45000]
    payload = {
        "model": settings.get("llm_model") or "gpt-4o-mini",
        "temperature": 0.4,
        "messages": [{"role": "user", "content": _prompt(transcript, count, f"{min_len:.0f}-{max_len:.0f} seconds")}],
    }
    url = (settings.get("llm_base_url") or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    try:
        with httpx.Client(timeout=90) as client:
            res = client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"})
            res.raise_for_status()
            content = res.json()["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
        data = json.loads(content)
        raw_clips = data.get("clips", [])
    except Exception as exc:  # fall back to heuristic silently but log
        print(f"[llm] falling back to heuristic: {exc}")
        return None
    clips = []
    for c in raw_clips:
        try:
            start, end = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        start, end = txt.snap_boundaries(words, start, end)
        if end - start < 8:
            continue
        seg_words = txt.words_between(words, start, end)
        if not seg_words:
            continue
        clips.append({
            "start": start, "end": end,
            "score": round(float(c.get("score", 70)), 1),
            "title": str(c.get("title") or _title_from(seg_words))[:120],
            "reason": str(c.get("reason") or "")[:300],
            "words": seg_words,
            "provider": "llm",
        })
    return clips or None


def detect_clips(words: list[dict], count: int = 7, settings: dict | None = None,
                 min_len: float = 20, max_len: float = 60,
                 provider: str = "auto") -> tuple[list[dict], str]:
    """Return (clips, provider_used)."""
    settings = settings or {}
    if provider in ("auto", "llm"):
        clips = _llm_clips(words, count, settings, min_len, max_len)
        if clips:
            return clips, "llm"
        if provider == "llm":
            raise RuntimeError("LLM clip detection failed. Check the API key/model in Settings.")
    clips = heuristic_clips(words, count, min_len, max_len)
    for c in clips:
        c["provider"] = "heuristic"
        c["reason"] = c.get("reason") or "High keyword density and clean sentence boundaries."
    return clips, "heuristic"
