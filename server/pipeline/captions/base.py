"""Caption style model and ASS generation.

Adding a new caption look:
  1. Add a preset dict in presets.py (any of the animation modes below).
  2. Or create a subclass of CaptionRenderer and register it in RENDERERS.

Animation modes are independent from colours/typography, so a preset is just
data. `ass_writer.build_ass()` is the only entry point the renderer needs.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import asdict, dataclass, field, fields
from typing import Iterable

POSITIONS = {"top": 0.24, "middle": 0.5, "bottom": 0.78}

ANIMATIONS = ("pop", "karaoke", "typewriter", "fade", "none")


_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def ass_color(hex_color: str, alpha: int = 0) -> str:
    """#RRGGBB or #RRGGBBAA -> &HAABBGGRR (ASS uses BGR order).

    Anything that is not valid hex falls back to white / full opacity so a bad
    colour in a saved preset can never produce a broken .ass file.
    """
    h = (hex_color or "").strip().lstrip("#")
    if not _HEX_RE.match(h):
        h = "FFFFFF"
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    elif len(h) == 4:  # #RGBA
        h = "".join(c * 2 for c in h)
    if len(h) == 8:
        aa = int(h[6:8], 16)
        alpha = 255 - aa
        h = h[:6]
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha & 0xFF:02X}{b}{g}{r}".upper()


def ass_alpha_from_opacity(opacity: float) -> int:
    opacity = max(0.0, min(1.0, float(opacity)))
    return round((1.0 - opacity) * 255)


def escape_ass(text: str) -> str:
    text = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    return text.replace("\n", "\\N")


@dataclass
class CaptionStyle:
    preset: str = "bold_impact"
    animation: str = "pop"
    font: str = "Archivo Black"
    font_size: int = 92
    uppercase: bool = True
    primary_color: str = "#FFFFFF"
    highlight_color: str = "#FFE600"
    outline_color: str = "#000000"
    outline_width: float = 10
    shadow: float = 3
    shadow_color: str = "#000000"
    glow: float = 0
    glow_color: str = "#00E5FF"
    blur_edges: float = 0
    position: str = "bottom"
    offset: float | None = None          # 0..1 of frame height (overrides position)
    words_per_line: int = 2
    max_chars: int = 24
    line_spacing: float = 1.0
    letter_spacing: float = 0
    box: bool = False
    box_color: str = "#000000"
    box_opacity: float = 0.55
    box_padding: float = 14
    emoji: bool = False
    emoji_every: int = 14
    emoji_scale: int = 100
    word_hold: float = 0.10              # seconds a word stays after it ends
    pop_scale: int = 118                 # % scale for the active word
    pop_ms: int = 90
    stroke_style: int = 1
    margin_side: int = 90

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "CaptionStyle":
        data = data or {}
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in data.items() if k in known}
        style = cls(**clean)
        style.words_per_line = max(1, min(3, int(style.words_per_line)))
        style.font_size = max(18, min(200, int(style.font_size)))
        style.animation = style.animation if style.animation in ANIMATIONS else "pop"
        style.position = style.position if style.position in POSITIONS else "bottom"
        return style

    def caption_y(self, frame_h: int = 1920) -> float:
        base = POSITIONS.get(self.position, 0.78)
        frac = base if self.offset is None else self.offset
        return max(60.0, min(frame_h - 60.0, frac * frame_h))


def _line_pos(style: CaptionStyle, frame_w: int = 1080, frame_h: int = 1920) -> tuple[float, float, int]:
    y = style.caption_y(frame_h)
    if style.position == "top":
        an = 8  # top-center, text grows downward
    elif style.position == "middle":
        an = 5
    else:
        an = 2
    return frame_w / 2, y, an


def _box_tags(style: CaptionStyle, alpha: int) -> str:
    if not style.box:
        return ""
    padding = max(0, int(style.box_padding))
    return (f"\\bord{padding * 2}\\shad0\\3c{ass_color(style.box_color)}\\4c{ass_color(style.box_color)}"
            f"\\4a&H{alpha:02X}&\\3a&H{alpha:02X}&")


def _base_tags(style: CaptionStyle, an: int, x: float, y: float) -> str:
    tags = (
        f"\\an{an}\\pos({x:.1f},{y:.1f})"
        f"\\fn{escape_ass(style.font)}"
        f"\\fs{int(style.font_size)}"
        f"\\bord{style.outline_width:g}\\shad{style.shadow:g}"
        f"\\1c{ass_color(style.primary_color)}"
        f"\\3c{ass_color(style.outline_color)}"
        f"\\4c{ass_color(style.shadow_color)}"
        f"\\fsp{style.letter_spacing:g}"
    )
    if style.box:
        tags += _box_tags(style, ass_alpha_from_opacity(style.box_opacity))
    if style.blur_edges:
        tags += f"\\be{style.blur_edges:g}"
    if style.glow:
        tags += (f"\\bord{style.outline_width + style.glow:g}"
                 f"\\3c{ass_color(style.glow_color)}\\3a&H60&"
                 f"\\shad{style.shadow + style.glow:g}")
    if style.stroke_style == 3:
        tags += "\\3a&HFF&"
    return tags


def _word_text(style: CaptionStyle, word: dict) -> str:
    t = word.get("text", "")
    if style.uppercase:
        t = t.upper()
    emoji = word.get("emoji")
    return f"{t} {emoji}" if emoji else t


class CaptionRenderer:
    """Base renderer: produces ASS Dialogue lines."""

    animation = "none"

    def __init__(self, style: CaptionStyle, frame_w: int = 1080, frame_h: int = 1920):
        self.style = style
        self.frame_w = frame_w
        self.frame_h = frame_h
        self.x, self.y, self.an = _line_pos(style, frame_w, frame_h)

    def events_for_line(self, line: list[dict]) -> list[tuple[float, float, str]]:
        raise NotImplementedError

    # helpers -------------------------------------------------------------
    def prefix(self) -> str:
        """Position + typography tags prepended to every dialogue line."""
        return "{" + _base_tags(self.style, self.an, self.x, self.y) + "}"

    def _active_text(self, line: list[dict], active: int) -> str:
        s = self.style
        parts: list[str] = []
        hi = ass_color(s.highlight_color)
        base = ass_color(s.primary_color)
        for i, w in enumerate(line):
            text = escape_ass(_word_text(s, w))
            if i == active:
                scale = s.pop_scale
                parts.append(
                    f"{{\\c{hi}\\fscx{scale}\\fscy{scale}"
                    f"\\t(0,{s.pop_ms},\\fscx100\\fscy100)}}{text}"
                    f"{{\\c{base}}}"
                )
            else:
                parts.append(f"{{\\c{base}}}{text}")
        return self.prefix() + " ".join(parts)

    def _plain_text(self, line: list[dict]) -> str:
        return self.prefix() + " ".join(escape_ass(_word_text(self.style, w)) for w in line)

    def _line_start(self, line: list[dict]) -> float:
        return line[0]["start"]

    def _line_end(self, line: list[dict]) -> float:
        return line[-1]["end"] + self.style.word_hold


class PopRenderer(CaptionRenderer):
    """Whole line stays on screen; one word is highlighted and pops."""

    animation = "pop"

    def events_for_line(self, line):
        out = []
        line_end = self._line_end(line)
        for i, w in enumerate(line):
            start = w["start"]
            end = line[i + 1]["start"] if i + 1 < len(line) else line_end
            if end - start < 0.1:
                end = start + 0.1
            out.append((start, end, self._active_text(line, i)))
        return out


class FadeRenderer(CaptionRenderer):
    animation = "fade"

    def events_for_line(self, line):
        text = self._plain_text(line)
        start, end = self._line_start(line), self._line_end(line)
        return [(start, end, f"{{\\fad(90,90)}}{text}")]


class TypewriterRenderer(CaptionRenderer):
    animation = "typewriter"

    def events_for_line(self, line):
        out = []
        for i, w in enumerate(line):
            start = w["start"]
            end = line[i + 1]["start"] if i + 1 < len(line) else line[i]["end"] + self.style.word_hold
            prefix = " ".join(escape_ass(_word_text(self.style, x)) for x in line[: i + 1])
            cursor = "▌" if i < len(line) - 1 else ""
            out.append((start, max(start + 0.1, end),
                        self.prefix() + f"{{\\fad(40,0)}}{prefix}{cursor}"))
        return out


class KaraokeRenderer(CaptionRenderer):
    animation = "karaoke"

    def events_for_line(self, line):
        s = self.style
        parts = []
        for w in line:
            dur = max(5, int(round((w["end"] - w["start"]) * 100)))
            parts.append(f"{{\\kf{dur}}}{escape_ass(_word_text(s, w))} ")
        # unspoken text uses secondary colour, sung text primary (highlight)
        text = (
            self.prefix()
            + f"{{\\1c{ass_color(s.highlight_color)}\\2c{ass_color(s.primary_color)}}}"
            + "".join(parts).rstrip()
        )
        return [(self._line_start(line), self._line_end(line), text)]


class NeonPopRenderer(PopRenderer):
    animation = "pop"

    def events_for_line(self, line):
        events = super().events_for_line(line)
        out = []
        for start, end, text in events:
            glow = (f"{{\\be1\\blur8}}")
            out.append((start, end, text))
        return out


RENDERERS: dict[str, type[CaptionRenderer]] = {
    "pop": PopRenderer,
    "fade": FadeRenderer,
    "typewriter": TypewriterRenderer,
    "karaoke": KaraokeRenderer,
    "none": FadeRenderer,
}


def get_renderer(style: CaptionStyle, frame_w: int = 1080, frame_h: int = 1920) -> CaptionRenderer:
    cls = RENDERERS.get(style.animation, PopRenderer)
    return cls(style, frame_w, frame_h)


def _header(style: CaptionStyle, play_w: int, play_h: int) -> str:
    s = style
    box_alpha = ass_alpha_from_opacity(s.box_opacity)
    base = (
        f"Style: Default,{escape_ass(s.font)},{int(s.font_size)},"
        f"{ass_color(s.primary_color)},{ass_color(s.primary_color)},"
        f"{ass_color(s.outline_color)},{ass_color(s.shadow_color)},"
        f"0,0,0,0,100,100,{s.letter_spacing:g},0,{s.stroke_style},"
        f"{s.outline_width:g},{s.shadow:g},2,{s.margin_side},{s.margin_side},60,1"
    )
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_w}
PlayResY: {play_h}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
Collisions: Normal

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{base}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(words: list[dict], style: CaptionStyle, duration: float,
              play_w: int = 1080, play_h: int = 1920) -> str:
    """Build a complete .ass file for one clip (times relative to clip start)."""
    from .. import text as txt
    from ..media import ass_time

    lines_words = txt.chunk_words(words, style.words_per_line, style.max_chars)
    renderer = get_renderer(style, play_w, play_h)
    events: list[tuple[float, float, str]] = []
    for line in lines_words:
        if not line:
            continue
        for start, end, text in renderer.events_for_line(line):
            if end <= 0 or start >= duration:
                continue
            start = max(0.0, start)
            end = min(duration, end)
            if end - start < 0.08:
                end = min(duration, start + 0.08)
            if end > start:
                events.append((start, end, text))
    events.sort(key=lambda e: e[0])
    # prevent later-starting lines from stacking on top of earlier ones
    cleaned: list[tuple[float, float, str]] = []
    for s, e, t in events:
        if cleaned and s < cleaned[-1][1] - 1e-3 and cleaned[-1][0] < s:
            prev_s, prev_e, prev_t = cleaned[-1]
            if s - prev_s > 0.08:
                cleaned[-1] = (prev_s, s, prev_t)
        cleaned.append((s, e, t))
    events = [ev for ev in cleaned if ev[1] - ev[0] > 0.05]
    header = _header(style, play_w, play_h)
    body = "\n".join(
        f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Default,,0,0,0,,{t}"
        for s, e, t in events
    )
    return header + body + "\n"
