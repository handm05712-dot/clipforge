"""Stage B/D judge: transcript → candidates → per-clip metadata.

Two separate jobs, both validated against a schema and retried once:

  * `find_moments`    scan a transcript window, propose moments worth clipping
  * `judge_candidates` score each candidate on 8 dimensions and write the copy

The provider is whatever speaks OpenAI's chat-completions shape, exactly like
the rest of the project (`store.load_settings()` holds base URL, key, model).
Without a key everything degrades to keywords + signals, which still works.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ... import store

CONTENT_TYPES = {
    "gaming": "Gaming / stream reaction",
    "podcast": "Podcast / interview",
    "commentary": "Commentary",
    "education": "Education",
    "general": "General",
}

DIMENSIONS = ("hook", "emotion", "payoff", "standalone", "shareable", "novelty", "pacing", "relevance")


class JudgeError(RuntimeError):
    pass


def settings() -> dict:
    return store.load_settings()


def enabled() -> bool:
    return bool(settings().get("llm_api_key"))


# ---------------------------------------------------------------- prompting --

FIND_SYSTEM = """You find moments worth clipping from a long video transcript.

Be strict. Most transcripts contain only a handful of genuinely clip-worthy
moments. A moment qualifies only if it has a strong opening line AND a payoff.

Rules:
- Return between {lo} and {hi} moments.
- Each moment is {min_len}-{max_len} seconds long.
- `start` is the timestamp of the first word of the hook. `end` is after the payoff.
- Never start on filler ("um", "so", "yeah").
- Prefer self-contained moments over references to earlier in the video.
- Respect the viewer's topics when given, but do not invent matches.

Return JSON only, no prose, in this shape:
{{"moments": [{{"start": 0.0, "end": 0.0, "reason": "", "hook_line": ""}}]}}"""

JUDGE_SYSTEM = """You score short-form clip candidates. You are strict.

Scoring guidance:
- Most moments score below 60. That is expected and correct.
- Above 80 requires BOTH a strong hook in the first 3 seconds AND a real payoff.
- 90+ is rare: reserve it for moments you would personally repost.
- Judge each dimension 0-100:
  hook, emotion, payoff, standalone, shareable, novelty, pacing, relevance.

Return JSON only, in exactly this shape, one entry per candidate in the same order:
{{"candidates": [{{
  "start": 0.0, "end": 0.0,
  "scores": {{"hook": 0, "emotion": 0, "payoff": 0, "standalone": 0,
             "shareable": 0, "novelty": 0, "pacing": 0, "relevance": 0}},
  "total": 0,
  "title": "", "hook_line": "", "reason": "",
  "hashtags": [], "best_platform": ""
}}]}}

`title` max 60 characters. `hook_line` is the on-screen text for the first 3
seconds. `hashtags` is 5-8 without the # symbol. `best_platform` is one of
reels, shorts, tiktok. `total` is your overall 0-100, consistent with the
dimension scores."""


def _call(system: str, user: str, max_tokens: int = 4000) -> str:
    cfg = settings()
    key = cfg.get("llm_api_key")
    if not key:
        raise JudgeError("No model key configured")
    url = (cfg.get("llm_base_url") or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    payload = {
        "model": cfg.get("llm_model") or "gpt-4o-mini",
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    with httpx.Client(timeout=180) as client:
        res = client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"})
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]


def _json(text: str) -> Any:
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    start = text.find("{")
    if start > 0:
        text = text[start:]
    return json.loads(text)


# ------------------------------------------------------------- validation --


def validate_moments(data: Any, total: float) -> list[dict]:
    if not isinstance(data, dict):
        raise JudgeError("Judge did not return an object")
    raw = data.get("moments")
    if not isinstance(raw, list):
        raise JudgeError("Judge returned no moments list")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= start < end <= total + 5):
            continue
        out.append({
            "start": start,
            "end": end,
            "reason": str(item.get("reason") or "")[:400],
            "hook_line": str(item.get("hook_line") or "")[:120],
            "origin": "llm",
        })
    if not out:
        raise JudgeError("Judge returned no usable moments")
    return out


def validate_scores(data: Any, candidates: list[dict]) -> list[dict]:
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        raise JudgeError("Judge returned no candidates list")
    rows = data["candidates"]
    out = []
    for i, cand in enumerate(candidates):
        row = rows[i] if i < len(rows) and isinstance(rows[i], dict) else {}
        scores = row.get("scores") if isinstance(row.get("scores"), dict) else {}
        clean: dict[str, int] = {}
        for key in DIMENSIONS:
            try:
                clean[key] = int(max(0, min(100, float(scores.get(key, 50)))))
            except (TypeError, ValueError):
                clean[key] = 50
        try:
            total = float(row.get("total"))
        except (TypeError, ValueError):
            total = sum(clean.values()) / len(clean)
        hashtags = row.get("hashtags")
        out.append({
            **cand,
            "scores": clean,
            "total": max(0.0, min(100.0, total)),
            "title": str(row.get("title") or "")[:120],
            "hook_line": str(row.get("hook_line") or "")[:160],
            "reason": str(row.get("reason") or cand.get("reason") or "")[:400],
            "hashtags": [str(t) for t in hashtags][:10] if isinstance(hashtags, list) else [],
            "best_platform": str(row.get("best_platform") or "")[:20],
        })
    return out


def _retry(fn, *args, **kwargs):
    """Run a parse+validate pair, retrying once — the task's required behaviour."""
    try:
        return fn(*args, **kwargs)
    except (JudgeError, json.JSONDecodeError, httpx.HTTPError, KeyError, ValueError):
        return fn(*args, **kwargs)


# --------------------------------------------------------------- public API --


def find_moments(stamped_transcript: str, total: float, opts: dict,
                 count: int = 12, window_note: str = "") -> list[dict]:
    """Ask for moments in one transcript window. Returns [] when the LLM is off."""
    if not enabled():
        return []
    content = CONTENT_TYPES.get(opts.get("content_type") or "general", "General")
    topics = (opts.get("topics") or "").strip()
    system = FIND_SYSTEM.format(
        lo=max(3, count // 2), hi=max(4, count),
        min_len=int(opts.get("min_len") or 20), max_len=int(opts.get("max_len") or 60),
    )
    user = (
        f"Content type: {content}\n"
        + (f"Topics to prioritise: {topics}\n" if topics else "")
        + (f"{window_note}\n" if window_note else "")
        + "\nTranscript (timestamped, [mm:ss]):\n" + stamped_transcript
    )

    def attempt() -> list[dict]:
        return validate_moments(_json(_call(system, user)), total)

    try:
        return _retry(attempt)
    except Exception:
        return []


def judge_candidates(candidates: list[dict], opts: dict) -> list[dict]:
    """Score and describe candidates. Falls back to signal-only when no key."""
    if not candidates:
        return []
    if not enabled():
        return [_fallback_meta(c) for c in candidates]
    content = CONTENT_TYPES.get(opts.get("content_type") or "general", "General")
    topics = (opts.get("topics") or "").strip()
    blocks = []
    for i, cand in enumerate(candidates):
        blocks.append(
            f"--- candidate {i} ---\n"
            f"start {cand['start']:.1f}s  end {cand['end']:.1f}s  "
            f"length {cand['end'] - cand['start']:.1f}s\n"
            f"text: {(cand.get('text') or '')[:1200]}"
        )
    user = (
        f"Content type: {content}\n"
        + (f"Topics to prioritise: {topics}\n" if topics else "")
        + "\n" + "\n\n".join(blocks)
    )

    def attempt() -> list[dict]:
        raw = _call(JUDGE_SYSTEM, user, max_tokens=4000)
        return validate_scores(_json(raw), candidates)

    try:
        return _retry(attempt)
    except Exception:
        return [_fallback_meta(c) for c in candidates]


def _fallback_meta(cand: dict) -> dict:
    """No model: keep the clip, but say the copy is a placeholder."""
    text = (cand.get("text") or "").strip()
    first = re.split(r"(?<=[.!?])\s", text)[0] if text else ""
    return {
        **cand,
        "scores": {},
        "total": float(cand.get("total") or 0),
        "title": (first or "Clip")[:60],
        "hook_line": (first or "")[:90],
        "reason": "Ranked from the available signals. Add a model key for a written rationale.",
        "hashtags": [],
        "best_platform": "reels",
        "provider": "signals",
    }


def estimate(transcript: str, opts: dict, minutes: float) -> dict:
    """Rough cost/time before a long job, shown in the UI."""
    # ~4 characters per token, and one judge call per 8 min window plus one
    # scoring pass over the final candidates
    windows = max(1, int(minutes // 8) + 1)
    in_tokens = int(len(transcript) / 4) + windows * 250
    out_tokens = windows * 350
    windows_note = f"{windows} transcript windows"
    if enabled():
        cfg = settings()
        model = cfg.get("llm_model") or "gpt-4o-mini"
    else:
        model = "off (signals only)"
    return {
        "windows": windows_note,
        "model": model,
        "est_input_tokens": in_tokens,
        "est_output_tokens": out_tokens,
        "est_minutes": round(minutes * 0.25 + 0.6, 1),
        "note": "Estimate only. Downloads and Whisper dominate on long sources.",
    }
