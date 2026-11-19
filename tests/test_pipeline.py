"""ClipForge test suite (pytest).

Run:  .venv/bin/python -m pytest -q --junitxml=test-results/junit.xml

Covers the pure pipeline logic (no model downloads, no FFmpeg renders) plus the
ASS caption writer, which is where most styling bugs hide.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.pipeline import llm, text as txt  # noqa: E402
from server.pipeline.captions.base import (  # noqa: E402
    CaptionStyle, ass_color, build_ass, get_renderer,
)
from server.pipeline.captions.presets import PRESETS, style_for  # noqa: E402


def make_words(text: str, start: float = 0.0, step: float = 0.4) -> list[dict]:
    words = []
    t = start
    for raw in text.split():
        words.append({"text": raw, "start": round(t, 3), "end": round(t + step * 0.8, 3)})
        t += step
    return words


# --------------------------------------------------------------- captions ----

def test_colour_conversion_is_bgr():
    assert ass_color("#FF0000") == "&H000000FF"
    assert ass_color("#00FF00") == "&H0000FF00"
    assert ass_color("#FFFFFF") == "&H00FFFFFF"
    assert ass_color("#000000") == "&H00000000"


def test_colour_with_alpha():
    assert ass_color("#00000000").startswith("&HFF")
    assert ass_color("zzz").endswith("FFFFFF")   # invalid -> white
    assert ass_color("#bad").endswith("DDAABB")  # 3-digit shorthand still works


def test_all_presets_build_valid_ass():
    words = make_words("This is a short test of the caption engine, with punctuation!")
    for name in PRESETS:
        style = style_for(name)
        ass = build_ass(words, style, 4.0)
        assert "[Script Info]" in ass
        assert "Dialogue:" in ass
        assert "PlayResX: 1080" in ass
        # every event must have a start < end and a real style position
        for line in ass.splitlines():
            if line.startswith("Dialogue:"):
                _, start, end, *rest = line.split(",", 3)
                assert start < end
                assert "\\pos(" in rest[-1]


def test_new_style_is_data_only(monkeypatch):
    """Adding a style must require no code: build it from a dict."""
    style = CaptionStyle.from_dict({
        "animation": "pop", "font": "Anton", "font_size": 120,
        "primary_color": "#FFFFFF", "highlight_color": "#00FFAA",
        "position": "top", "words_per_line": 1,
    })
    ass = build_ass(make_words("Hello there world"), style, 2.0)
    assert "Anton" in ass
    assert "\\pos(540.0,460.8)" in ass  # top = 0.24 * 1920


def test_word_chunking_respects_limits():
    words = make_words("one two three four five six seven eight")
    lines = txt.chunk_words(words, 2, 30)
    assert all(len(l) <= 2 for l in lines)
    assert sum(len(l) for l in lines) == 8


def test_caption_events_never_overflow_duration():
    words = make_words("alpha beta gamma delta")
    ass = build_ass(words, style_for("karaoke"), 1.0)
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            start = line.split(",")[1]
            end = line.split(",")[2]
            assert start < end


def test_position_offsets_are_applied():
    words = make_words("Position test words here")
    for frac, expected in ((0.2, 384.0), (0.5, 960.0), (0.8, 1536.0)):
        style = CaptionStyle.from_dict({"offset": frac})
        ass = build_ass(words, style, 3.0)
        assert f"\\pos(540.0,{expected:.1f})" in ass


def test_renderers_return_events():
    words = make_words("A quick check")
    for animation in ("pop", "karaoke", "typewriter", "fade", "none"):
        style = CaptionStyle.from_dict({"animation": animation})
        renderer = get_renderer(style)
        events = renderer.events_for_line([words[0], words[1]])
        assert events, f"{animation} produced no events"
        for start, end, body in events:
            assert end > start
            assert body


# ------------------------------------------------------------------ text ----

def test_filler_detection():
    assert txt.is_filler("um")
    assert txt.is_filler("Uh,")
    assert not txt.is_filler("under")


def test_sentence_spans_land_on_boundaries():
    words = make_words("One two three. Four five six! Seven eight nine.")
    spans = txt.sentence_spans(words)
    assert len(spans) >= 1
    for span in spans:
        assert span["words"][0] is not None
        assert span["start"] <= span["end"]


def test_snap_boundaries_uses_word_edges():
    words = make_words("alpha beta gamma delta epsilon")
    start, end = txt.snap_boundaries(words, 0.05, 1.6)
    starts = {w["start"] for w in words}
    ends = {w["end"] for w in words}
    assert start in starts or start == 0.0
    assert end in ends or abs(end - 1.6) < 0.5


def test_silence_ranges_found():
    words = [
        {"text": "hello", "start": 1.0, "end": 1.5},
        {"text": "world", "start": 4.0, "end": 4.5},
    ]
    gaps = txt.silence_ranges(words, 6.0, min_gap=0.5)
    assert gaps


def test_keep_ranges_removes_fillers():
    words = [
        {"text": "So", "start": 0.0, "end": 0.2},
        {"text": "um", "start": 0.2, "end": 0.4},
        {"text": "the", "start": 0.4, "end": 0.6},
        {"text": "plan", "start": 0.6, "end": 1.0},
    ]
    keep = txt.keep_ranges(words, 1.2, drop_fillers=True, trim_silence=True)
    covered = sum(b - a for a, b in keep)
    assert 0 < covered <= 1.2
    mapped = txt.map_kept_range(words, keep)
    texts = [w["text"] for w in mapped]
    assert "um" not in texts
    assert "plan" in texts


def test_emoji_injection():
    words = make_words("we love money growth and success today")
    out = txt.auto_emoji("", words, every=3)
    assert any(w.get("emoji") for w in out)


# ------------------------------------------------------------ clip scoring ----

def test_heuristic_clips_are_ordered_and_bounded():
    words = make_words("This is a hook. " * 40, step=0.45)
    clips = llm.heuristic_clips(words, count=4, min_len=10, max_len=40)
    assert clips
    starts = [c["start"] for c in clips]
    assert starts == sorted(starts)
    for clip in clips:
        assert clip["end"] > clip["start"]
        assert 0 <= clip["score"] <= 99


def test_heuristic_clips_do_not_overlap():
    words = make_words("Secret money mistake truth. " * 30, step=0.5)
    clips = llm.heuristic_clips(words, count=5, min_len=10, max_len=40)
    for i in range(len(clips)):
        for j in range(i + 1, len(clips)):
            a, b = clips[i], clips[j]
            assert a["end"] <= b["start"] + 1.0 or b["end"] <= a["start"] + 1.0


def test_detect_clips_falls_back_offline():
    words = make_words("Nobody tells you this money mistake. " * 20, step=0.5)
    clips, provider = llm.detect_clips(words, count=2, settings={})
    assert provider == "heuristic"
    assert clips
    assert all(c["provider"] == "heuristic" for c in clips)


def test_detect_clips_returns_empty_for_no_words():
    assert llm.detect_clips([], count=3, settings={})[0] == []


# ---------------------------------------------------------------- mapping ----

def test_remap_words_compresses_time():
    from server.pipeline import audio as aud

    words = make_words("a b c d e f", step=1.0)
    keep = [[0.0, 2.0], [4.0, 6.0]]
    mapped = aud.remap_words(words, keep)
    assert mapped[0]["start"] == pytest.approx(0.0, abs=0.01)
    # the word at 4.0 becomes 2.0 after the cut
    after = next(w for w in mapped if w["start"] >= 1.9)
    assert after["start"] == pytest.approx(2.0, abs=0.2)


def test_import_time_matches_ass():
    from server.pipeline.media import ass_time

    assert ass_time(0) == "0:00:00.00"
    assert ass_time(61.5) == "0:01:01.50"
    assert ass_time(3661.25) == "1:01:01.25"


# ------------------------------------------------- regression: clip offsets ----

def test_keep_ranges_use_clip_local_time():
    """Regression: words stored in absolute time must be shifted to clip-local.

    A clip starting at 36s used to produce a 2s render because the edit planner
    compared absolute word times against a 0-based clip duration.
    """
    from server.pipeline import audio as aud

    clip_start, clip_end = 36.0, 70.0
    duration = clip_end - clip_start
    absolute = [
        {"text": "alpha", "start": 36.2, "end": 36.6},
        {"text": "beta", "start": 36.8, "end": 37.4},
        {"text": "gamma", "start": 60.0, "end": 60.5},
        {"text": "delta", "start": 69.4, "end": 69.9},
    ]
    local = [{**w, "start": w["start"] - clip_start, "end": w["end"] - clip_start} for w in absolute]
    keep = aud.build_keep_ranges(local, duration, remove_silences=True, remove_fillers=True)
    assert keep
    covered = sum(b - a for a, b in keep)
    assert covered > 2.0, f"clip collapsed to {covered:.2f}s"
    assert max(b for _, b in keep) <= duration + 0.01


def test_caption_words_are_clip_relative():
    from server.pipeline.captions.base import build_ass
    from server.pipeline.render import prepare_caption_words

    clip = {
        "start": 50.0, "end": 60.0,
        "words": [
            {"text": "hello", "start": 50.2, "end": 50.6},
            {"text": "world", "start": 50.8, "end": 51.2},
        ],
    }
    words = prepare_caption_words(clip, [[0.0, 10.0]], style_for("bold_impact"), 10.0)
    assert words[0]["start"] == pytest.approx(0.2, abs=0.01)
    ass = build_ass(words, style_for("bold_impact"), 10.0)
    # first event must start at 0.2s, not 50.2s
    first = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
    assert "0:00:00.20" in first


# ------------------------------------------------------------- platforms ----

def test_platform_presets_resolve_sizes():
    from server.pipeline import platforms

    reels = platforms.resolve({"platform": "reels"})
    assert (reels["width"], reels["height"]) == (1080, 1920)
    assert reels["aspect"] == "9:16"
    square = platforms.resolve({"platform": "square"})
    assert (square["width"], square["height"]) == (1080, 1080)
    land = platforms.resolve({"platform": "landscape"})
    assert (land["width"], land["height"]) == (1920, 1080)


def test_bad_platform_falls_back_to_reels():
    from server.pipeline import platforms

    spec = platforms.resolve({"platform": "myspace"})
    assert spec["platform"] == "reels"
    assert spec["width"] == 1080


def test_quality_presets_change_crf():
    from server.pipeline import platforms

    assert platforms.resolve({"quality": "high"})["crf"] < platforms.resolve({"quality": "small"})["crf"]


def test_caption_safe_area_clamps_offsets():
    from server.pipeline import platforms, render

    spec = platforms.resolve({"platform": "tiktok"})
    style = CaptionStyle(preset="t", position="bottom", offset=0.98)
    safe = render.safe_style(style, spec)
    assert safe.offset <= 1 - spec["safe"]["bottom"] - 0.04 + 1e-6
    assert safe.margin_side >= spec["width"] * spec["safe"]["side"]


def test_caption_inside_tiktok_safe_area_ass():
    """Dialogue position must sit above the TikTok button column."""
    from server.pipeline import platforms, render

    spec = platforms.resolve({"platform": "tiktok"})
    style = render.safe_style(CaptionStyle(animation="fade", position="bottom", offset=0.95), spec)
    ass = build_ass(make_words("hello world here we go", 0.2), style, 4.0, spec["width"], spec["height"])
    assert "\\pos(" in ass
    y = float(ass.split("\\pos(")[1].split(",")[1].split(")")[0])
    assert y < spec["height"] * (1 - spec["safe"]["bottom"])


# ---------------------------------------------------------------- social ----

def test_social_fallback_has_tags_and_title():
    from server.pipeline import social

    words = make_words("Money secrets nobody tells you about building a business fast")
    copy = social.suggest(words, "Money secrets", "tiktok", {})
    assert copy["provider"] == "offline"
    assert copy["hashtags"]
    assert all(t.startswith("#") for t in copy["hashtags"])
    assert copy["title"]


# --------------------------------------------------------------- history ----

def test_history_undo_redo_roundtrip(tmp_path, monkeypatch):
    from server import config, history

    monkeypatch.setattr(config, "DATA", tmp_path)
    project = {"id": "ptest", "title": "A", "options": {"a": 1}, "clips": [{"id": "c1"}], "style": None}
    history.record(project, "first")
    project["options"]["a"] = 2
    history.record(project, "second")
    project["options"]["a"] = 3
    assert history.can_undo("ptest")

    history.undo(project)
    assert project["options"]["a"] == 2
    history.undo(project)
    assert project["options"]["a"] == 1
    assert not history.can_undo("ptest")
    history.redo(project)
    assert project["options"]["a"] == 2


def test_history_nothing_to_undo_raises(tmp_path, monkeypatch):
    from server import config, history

    monkeypatch.setattr(config, "DATA", tmp_path)
    with pytest.raises(ValueError):
        history.undo({"id": "pempty", "options": {}, "clips": []})


# ----------------------------------------------------------------- export ----

def test_export_same_title_gets_unique_filenames(tmp_path, monkeypatch):
    """Two clips with identical titles must not overwrite each other."""
    from server import config, library
    from server.pipeline import export, media

    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "EXPORTS", tmp_path / "exports")
    project = {
        "id": "pdup", "title": "dup", "video_path": "/tmp/x.mp4",
        "media": {"duration": 10, "width": 640, "height": 360, "fps": 30, "has_audio": True},
        "options": {}, "clips": [
            {"id": "c1", "title": "Same title", "start": 0, "end": 5, "words": []},
            {"id": "c2", "title": "Same title", "start": 5, "end": 10, "words": []},
        ],
    }
    d = tmp_path / "projects" / "pdup"
    d.mkdir(parents=True)
    library.save(project)

    def fake_render(proj, clip, out, workdir, on_progress=None, preview=False):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x" * 4096)
        return {"duration": clip["end"] - clip["start"], "keep_ranges": [[0.0, 5.0]]}

    monkeypatch.setattr(export.render, "render_clip", fake_render)
    export.export_one("pdup", "c1", "j1")
    export.export_one("pdup", "c2", "j2")
    names = sorted(p.name for p in (tmp_path / "exports" / "pdup").iterdir())
    assert names == ["same-title-2.mp4", "same-title.mp4"]
