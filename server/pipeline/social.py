"""Per-clip social copy: title, caption and hashtag suggestions.

Uses the configured LLM when available and always has a deterministic offline
fallback so the feature works without an API key.
"""
from __future__ import annotations

import json
import re

import httpx

from . import text as txt

STOP = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "it", "this", "that", "you", "your",
    "i", "we", "they", "he", "she", "my", "our", "at", "as", "if", "so", "just",
    "about", "from", "have", "has", "had", "do", "does", "did", "not", "get",
}

PLATFORM_TAGS = {
    "reels": ["#reels", "#reelsinstagram", "#contentcreator"],
    "tiktok": ["#fyp", "#foryou", "#tiktok"],
    "shorts": ["#shorts", "#youtubeshorts", "#creator"],
    "square": ["#instagood", "#socialmedia"],
    "landscape": ["#youtube", "#video"],
}


def _keywords(words: list[dict], n: int = 6) -> list[str]:
    counts: dict[str, int] = {}
    for w in words:
        t = re.sub(r"[^A-Za-z0-9]", "", w.get("text", "")).strip().lower()
        if len(t) < 4 or t in STOP:
            continue
        counts[t] = counts.get(t, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    return [k for k, _ in ranked[:n]]


def _fallback(words: list[dict], title: str, platform: str) -> dict:
    kw = _keywords(words)
    hook = title.strip() or (txt.join_words(words)[:70] if words else "New clip")
    hook = re.sub(r"\s+", " ", hook).strip()
    if len(hook) > 90:
        hook = hook[:87].rsplit(" ", 1)[0] + "…"
    body = txt.join_words(words)
    summary = body[:180].rsplit(" ", 1)[0] + "…" if len(body) > 180 else body
    tags = [f"#{k}" for k in kw[:4]] + PLATFORM_TAGS.get(platform, [])[:3]
    # dedupe while keeping order
    seen, out_tags = set(), []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            out_tags.append(t)
    return {
        "title": hook,
        "caption": f"{hook}\n\n{summary}".strip(),
        "hashtags": out_tags,
        "provider": "offline",
    }


def _llm(words: list[dict], title: str, platform: str, settings: dict) -> dict | None:
    key = settings.get("llm_api_key")
    if not key:
        return None
    body = txt.join_words(words)[:3500]
    prompt = (
        "Write short-form social copy for this clip.\n"
        f"Platform: {platform}. Existing title: {title}\n"
        "Return ONLY JSON with keys: title (max 8 words, hooky), caption "
        "(1-2 short sentences, conversational, no emojis spam), hashtags "
        "(array of 5-8 lowercase tags starting with #).\n\n"
        f"TRANSCRIPT:\n{body}"
    )
    url = (settings.get("llm_base_url") or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    try:
        with httpx.Client(timeout=45) as client:
            res = client.post(url, json={
                "model": settings.get("llm_model") or "gpt-4o-mini",
                "temperature": 0.7,
                "messages": [{"role": "user", "content": prompt}],
            }, headers={"Authorization": f"Bearer {key}"})
            res.raise_for_status()
            content = res.json()["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
        data = json.loads(content)
        tags = [t if str(t).startswith("#") else f"#{t}" for t in (data.get("hashtags") or [])][:8]
        return {
            "title": str(data.get("title") or title)[:120],
            "caption": str(data.get("caption") or "")[:400],
            "hashtags": [str(t).replace(" ", "") for t in tags],
            "provider": "llm",
        }
    except Exception as exc:  # network/parse failures fall back silently
        print(f"[social] falling back to offline copy: {exc}")
        return None


def suggest(words: list[dict], title: str, platform: str, settings: dict | None = None) -> dict:
    settings = settings or {}
    return _llm(words, title, platform, settings) or _fallback(words, title, platform)
