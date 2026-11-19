"""Caption presets. Each preset is plain CaptionStyle data — copy one to add a style."""
from __future__ import annotations

from .base import CaptionStyle

PRESETS: dict[str, dict] = {
    "bold_impact": {
        "preset": "bold_impact", "animation": "pop", "font": "Archivo Black",
        "font_size": 96, "uppercase": True, "primary_color": "#FFFFFF",
        "highlight_color": "#FFD400", "outline_color": "#000000", "outline_width": 12,
        "shadow": 0, "position": "bottom", "offset": 0.76, "words_per_line": 2,
        "max_chars": 22, "pop_scale": 122, "pop_ms": 80, "word_hold": 0.12,
    },
    "minimal_clean": {
        "preset": "minimal_clean", "animation": "fade", "font": "Inter",
        "font_size": 64, "uppercase": False, "primary_color": "#FFFFFF",
        "highlight_color": "#FFFFFF", "outline_color": "#000000", "outline_width": 0,
        "shadow": 2, "shadow_color": "#000000", "position": "bottom", "offset": 0.82,
        "words_per_line": 3, "max_chars": 30, "box": True, "box_color": "#0B0B0F",
        "box_opacity": 0.6, "box_padding": 16, "stroke_style": 1,
    },
    "neon_glow": {
        "preset": "neon_glow", "animation": "pop", "font": "Poppins",
        "font_size": 84, "uppercase": True, "primary_color": "#FFFFFF",
        "highlight_color": "#00FFE0", "outline_color": "#001418", "outline_width": 6,
        "glow": 12, "glow_color": "#00FFE0", "shadow": 0, "blur_edges": 1,
        "position": "middle", "offset": 0.55, "words_per_line": 1, "max_chars": 14,
        "pop_scale": 130, "pop_ms": 110, "letter_spacing": 1,
    },
    "karaoke": {
        "preset": "karaoke", "animation": "karaoke", "font": "Montserrat",
        "font_size": 72, "uppercase": True, "primary_color": "#FFFFFF",
        "highlight_color": "#FF3D81", "outline_color": "#000000", "outline_width": 8,
        "shadow": 2, "position": "bottom", "offset": 0.8, "words_per_line": 3,
        "max_chars": 28, "box": True, "box_color": "#000000", "box_opacity": 0.35,
        "box_padding": 12,
    },
    "typewriter": {
        "preset": "typewriter", "animation": "typewriter", "font": "Space Mono",
        "font_size": 60, "uppercase": False, "primary_color": "#F5F5F5",
        "highlight_color": "#F5F5F5", "outline_color": "#000000", "outline_width": 3,
        "shadow": 0, "position": "bottom", "offset": 0.78, "words_per_line": 3,
        "max_chars": 34, "font_size": 58, "box": True, "box_color": "#101014",
        "box_opacity": 0.75, "box_padding": 14, "stroke_style": 1,
    },
    "meme_yellow": {
        "preset": "meme_yellow", "animation": "pop", "font": "Anton",
        "font_size": 100, "uppercase": True, "primary_color": "#FFE600",
        "highlight_color": "#FFFFFF", "outline_color": "#000000", "outline_width": 10,
        "shadow": 0, "position": "top", "offset": 0.2, "words_per_line": 2,
        "max_chars": 20,
    },
}

DEFAULT_PRESET = "bold_impact"


def list_presets() -> list[dict]:
    return [CaptionStyle.from_dict(p).to_dict() for p in PRESETS.values()]


def style_for(name: str | None) -> CaptionStyle:
    data = PRESETS.get(name or DEFAULT_PRESET)
    if data is None:
        return CaptionStyle()
    return CaptionStyle.from_dict(data)
