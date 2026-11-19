"""Output presets: social platforms and aspect ratios.

Each preset bundles a resolution, a recommended clip length window and the
UI safe zones (fractions of the frame covered by app chrome) so captions and
watermarks never land under TikTok/Shorts/Reels buttons.
"""
from __future__ import annotations

ASPECTS: dict[str, dict] = {
    "9:16": {"label": "Vertical 9:16", "width": 1080, "height": 1920},
    "1:1": {"label": "Square 1:1", "width": 1080, "height": 1080},
    "16:9": {"label": "Landscape 16:9", "width": 1920, "height": 1080},
}

PLATFORMS: dict[str, dict] = {
    "reels": {
        "label": "Instagram Reels",
        "aspect": "9:16",
        "min_len": 15,
        "max_len": 60,
        "safe": {"top": 0.14, "bottom": 0.22, "side": 0.06},
        "note": "4:5 to 9:16 · up to 90s · keep captions above the caption bar",
    },
    "shorts": {
        "label": "YouTube Shorts",
        "aspect": "9:16",
        "min_len": 15,
        "max_len": 60,
        "safe": {"top": 0.10, "bottom": 0.18, "side": 0.06},
        "note": "Vertical · up to 3 min · leave room for the title row",
    },
    "tiktok": {
        "label": "TikTok",
        "aspect": "9:16",
        "min_len": 15,
        "max_len": 60,
        "safe": {"top": 0.12, "bottom": 0.24, "side": 0.07},
        "note": "Vertical · up to 10 min · right-hand buttons cover the edge",
    },
    "square": {
        "label": "Square feed post",
        "aspect": "1:1",
        "min_len": 10,
        "max_len": 60,
        "safe": {"top": 0.08, "bottom": 0.08, "side": 0.05},
        "note": "1:1 for feed posts and carousels",
    },
    "landscape": {
        "label": "Landscape / YouTube",
        "aspect": "16:9",
        "min_len": 10,
        "max_len": 90,
        "safe": {"top": 0.06, "bottom": 0.08, "side": 0.05},
        "note": "16:9 with larger side margins for captions",
    },
}

QUALITY: dict[str, dict] = {
    "high": {"label": "High quality", "crf": 16, "preset": "medium", "audio": "192k"},
    "balanced": {"label": "Balanced", "crf": 21, "preset": "medium", "audio": "160k"},
    "small": {"label": "Small file", "crf": 25, "preset": "fast", "audio": "128k"},
}


def resolve(options: dict | None) -> dict:
    """Turn saved options into a concrete render target."""
    options = options or {}
    name = options.get("platform")
    if name not in PLATFORMS:
        name = "reels"
    platform = PLATFORMS.get(name) or PLATFORMS["reels"]
    aspect = options.get("aspect") if options.get("aspect") in ASPECTS else platform["aspect"]
    dims = ASPECTS[aspect]
    quality = QUALITY.get(options.get("quality") or "balanced") or QUALITY["balanced"]
    safe = platform["safe"]
    return {
        "platform": name,
        "platform_label": platform["label"],
        "aspect": aspect,
        "width": int(options.get("width") or dims["width"]),
        "height": int(options.get("height") or dims["height"]),
        "crf": quality["crf"],
        "preset": quality["preset"],
        "audio_bitrate": quality["audio"],
        "safe": safe,
        "quality": options.get("quality") or "balanced",
    }


def list_presets() -> dict:
    return {"platforms": PLATFORMS, "aspects": ASPECTS, "quality": QUALITY}
