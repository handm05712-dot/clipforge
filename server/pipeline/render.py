"""Render a clip: reframe/reframe-plan, captions, music, SFX, watermark, H.264 export."""
from __future__ import annotations

import math
from pathlib import Path

from . import audio as aud
from . import fonts, media, platforms, text as txt
from .captions.base import CaptionStyle, build_ass
from .captions.presets import style_for

FPS = 30


def _sendcmd(plan: dict, key: str = "cf", w: int = 0, h: int = 0) -> str:
    """Build sendcmd script that moves the crop window along the plan."""
    lines = []
    kfs = plan.get("keyframes") or []
    crop_w = plan.get("crop", {}).get("w") or w
    crop_h = plan.get("crop", {}).get("h") or h
    for (t, cx, cy) in kfs:
        x = max(0.0, min(w - crop_w, cx - crop_w / 2))
        y = max(0.0, min(h - crop_h, cy - crop_h / 2))
        lines.append(f"{max(0.0, t):.3f} [enter] {key} x {x:.1f};")
        lines.append(f"{max(0.0, t):.3f} [enter] {key} y {y:.1f};")
    return "\n".join(lines) + ("\n" if lines else "")


def _select_expr(keep: list[list[float]], duration: float, offset: float = 0.0) -> str:
    if len(keep) == 1 and keep[0][0] <= 1e-6 and keep[0][1] >= duration - 0.01:
        return ""
    parts = [f"between(t,{a + offset:.3f},{b + offset:.3f})" for a, b in keep]
    return "+".join(parts)


def _subs_filter(ass_path: Path, fonts_dir: Path) -> str:
    p = media.filter_escape_path(ass_path)
    fd = media.filter_escape_path(fonts_dir)
    return f"subtitles=filename='{p}':fontsdir='{fd}'"


def safe_style(style: CaptionStyle, spec: dict) -> CaptionStyle:
    """Clamp the caption band inside the platform safe area.

    Prevents text being hidden under TikTok buttons or the Reels caption bar.
    """
    safe = spec.get("safe") or {}
    top = float(safe.get("top", 0.04))
    bottom = float(safe.get("bottom", 0.04))
    lo, hi = top + 0.04, 1.0 - bottom - 0.04
    if lo >= hi:
        lo, hi = 0.05, 0.95
    base = {"top": 0.24, "middle": 0.5, "bottom": 0.78}.get(style.position, 0.78)
    frac = base if style.offset is None else float(style.offset)
    if frac < lo or frac > hi:
        frac = max(lo, min(hi, frac))
    out = CaptionStyle.from_dict(style.to_dict())
    out.offset = round(frac, 4)
    # keep long lines inside the frame with the platform side margin
    side_frac = float(safe.get("side", 0.05))
    out.margin_side = max(int(out.margin_side), int(spec["width"] * side_frac))
    return out


def prepare_caption_words(clip: dict, keep: list[list[float]], style: CaptionStyle,
                          clip_duration: float) -> list[dict]:
    """Clip-local words -> edited timeline -> caption-ready words."""
    start = float(clip.get("start") or 0.0)
    words = clip.get("words_override") or clip.get("words") or []
    local = [
        {**w, "start": max(0.0, float(w["start"]) - start),
         "end": max(0.0, float(w["end"]) - start)}
        for w in words
    ]
    mapped = aud.remap_words(local, keep)
    mapped = [{**w, "start": max(0.0, min(clip_duration, w["start"])),
               "end": max(0.0, min(clip_duration, w["end"]))} for w in mapped]
    mapped = [w for w in mapped if w["end"] > w["start"] + 0.02]
    if style.emoji:
        mapped = txt.auto_emoji("", mapped, every=style.emoji_every)
    return mapped


def _sharpen(source_w: int, source_h: int, W: int, H: int) -> list[str]:
    """Counteract the softness of upscaling a small source into a big frame.

    A 480p phone clip cropped to 9:16 and blown up to 1080x1920 loses real
    detail; a light unsharp pass brings back the edges without the halos you
    get from pushing it harder. Large sources are left alone.
    """
    factor = min(source_w / max(1, W), source_h / max(1, H))
    if factor >= 0.95:
        return []
    if factor < 0.45:
        return ["unsharp=5:5:0.95:5:5:0.0"]
    return ["unsharp=5:5:0.55:5:5:0.0"]


def _layout_chain(vf: list[str], opts: dict, info: dict, spec: dict,
                  crop_plan: dict | None) -> str | None:
    """Video chain for the standard / gameplay layouts. Returns filter text."""
    W, H = spec["width"], spec["height"]
    reframe_mode = opts.get("reframe") or "face"
    bg_mode = bool(opts.get("blur_background"))
    if reframe_mode == "face" and crop_plan and not bg_mode:
        crop_w = crop_plan.get("crop", {}).get("w") or info["width"]
        crop_h = crop_plan.get("crop", {}).get("h") or info["height"]
        vf.append(f"crop@cf=w={int(crop_w)}:h={int(crop_h)}:x=0:y=0")
        vf.append(f"scale={W}:{H}:flags=lanczos")
        vf.extend(_sharpen(crop_w, crop_h, W, H))
    elif bg_mode:
        vf.append(
            f"split=2[bgf][fgf];"
            f"[bgf]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"boxblur=40:3,eq=brightness=-0.08[bg];"
            f"[fgf]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1"
        )
    else:
        vf.append(f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{H}:flags=lanczos")
        vf.extend(_sharpen(info["width"], info["height"], W, H))
    return None


def render_clip(project: dict, clip: dict, out_path: Path, workdir: Path,
                on_progress=None, preview: bool = False) -> dict:
    src = Path(project["video_path"]).resolve()
    info = project.get("media") or media.probe(src)
    clip_start = float(clip["start"])
    clip_end = float(clip["end"])
    duration = max(0.4, clip_end - clip_start)
    opts = project.get("options") or {}
    spec = platforms.resolve(opts)
    W, H = spec["width"], spec["height"]
    style = CaptionStyle.from_dict(clip.get("style") or project.get("style") or {})
    if not clip.get("style") and not project.get("style"):
        style = style_for(clip.get("style_preset") or "bold_impact")
    style = safe_style(style, spec)

    fonts.ensure_installed()
    fonts.ensure_font_file(style.font)   # download a missing Google Font before libass runs
    workdir.mkdir(parents=True, exist_ok=True)

    # 1. audio edit plan (silence + filler removal).
    # The planner works in clip-local seconds, but stored clip words are absolute.
    raw_words = clip.get("words_override") or clip.get("words") or []
    local_words = [
        {**w, "start": max(0.0, float(w["start"]) - clip_start),
         "end": max(0.0, float(w["end"]) - clip_start)}
        for w in raw_words
    ]
    local_words = [w for w in local_words if w["end"] > 0 and w["start"] < duration]
    keep_local = aud.build_keep_ranges(local_words, duration,
                                       bool(opts.get("remove_silences", False)),
                                       bool(opts.get("remove_fillers", True)))
    cut_duration = sum(b - a for a, b in keep_local)
    clip_duration = max(0.5, cut_duration)

    # 2. captions
    caption_words = prepare_caption_words(clip, keep_local, style, clip_duration)
    ass_text = build_ass(caption_words, style, clip_duration, W, H)
    ass_path = workdir / "captions.ass"
    ass_path.write_text(ass_text, encoding="utf-8")

    # 3. inputs: source, watermark, gameplay, music, sfx...
    cmd = ["-y", "-ss", f"{clip_start:.3f}", "-t", f"{duration:.3f}", "-i", str(src)]
    inputs: list[tuple[str, int]] = []       # (kind, ffmpeg input index)
    watermark = opts.get("watermark_path")
    if watermark and Path(watermark).exists():
        inputs.append(("watermark", 1))
        cmd += ["-i", str(watermark)]
    gameplay = opts.get("gameplay_path") if opts.get("layout") in ("gameplay_top", "gameplay_full") else None
    if gameplay and Path(gameplay).exists():
        idx = 1 + len(inputs)
        inputs.append(("gameplay", idx))
        cmd += ["-ss", f"{clip_start:.3f}", "-t", f"{duration:.3f}", "-i", str(gameplay)]
    music_index = None
    music = opts.get("music_path")
    if music and Path(music).exists():
        music_index = 1 + len(inputs)
        inputs.append(("music", music_index))
        cmd += ["-stream_loop", "-1", "-i", str(music)]
    sfx: list[tuple[int, dict]] = []
    for fx in (opts.get("sfx") or [])[:6]:
        p = fx.get("path")
        if p and Path(p).exists():
            idx = 1 + len(inputs)
            inputs.append(("sfx", idx))
            cmd += ["-i", str(p)]
            sfx.append((idx, fx))
    def idx_of(kind: str) -> int | None:
        return next((i for k, i in inputs if k == kind), None)

    # 4. video chain
    crop_plan = clip.get("crop_plan")
    layout = opts.get("layout") or "standard"
    source_w, source_h = info["width"], info["height"]
    pre_parts: list[str] = []          # full graph fragments (may contain ';')
    fc_parts: list[str] = []
    vf: list[str] = []
    in_label = "0:v"
    if info["fps"] and abs(info["fps"] - FPS) > 0.01:
        vf.append(f"fps={FPS}")
    vf.append("setsar=1")
    sendcmd_path = None
    gi = idx_of("gameplay")
    if layout == "gameplay_top" and gi is not None:
        top_h = media.even(H * 0.62)
        bot_h = H - top_h
        pre_parts.append(
            f"[{gi}:v]fps={FPS},scale={W}:{top_h}:force_original_aspect_ratio=increase,"
            f"crop={W}:{top_h},setsar=1[gp];"
            f"[0:v]fps={FPS},setsar=1,scale={W}:{bot_h}:force_original_aspect_ratio=increase,"
            f"crop={W}:{bot_h}[cam];"
            f"[gp][cam]vstack=inputs=2[basev]"
        )
        in_label = "basev"
    elif layout == "gameplay_full" and gi is not None:
        cam_w = media.even(W * 0.32)
        cam_h = media.even(cam_w * 9 / 16)
        pre_parts.append(
            f"[{gi}:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},setsar=1[gp];"
            f"[0:v]fps={FPS},setsar=1,crop=iw*0.42:ih:iw*0.29:0,"
            f"scale={cam_w}:{cam_h}:force_original_aspect_ratio=increase,"
            f"crop={cam_w}:{cam_h},format=rgba,"
            f"drawbox=x=0:y=0:w={cam_w}:h={cam_h}:color=black@0.35:t=4,"
            f"format=rgba[cam];"
            f"[gp][cam]overlay=W-w-28:H-h-160:shortest=1[basev]"
        )
        in_label = "basev"
    else:
        reframe_mode = opts.get("reframe") or "face"
        bg_mode = bool(opts.get("blur_background"))
        if reframe_mode == "face" and crop_plan and not bg_mode:
            crop_w = crop_plan.get("crop", {}).get("w") or source_w
            crop_h = crop_plan.get("crop", {}).get("h") or source_h
            sendcmd_path = workdir / "crop.cmd"
            sendcmd_path.write_text(_sendcmd(crop_plan, "cf", source_w, source_h))
            vf.append(f"sendcmd=f={media.filter_escape_path(sendcmd_path)}")
            vf.append(f"crop@cf=w={int(crop_w)}:h={int(crop_h)}:x=0:y=0")
            vf.append(f"scale={W}:{H}:flags=lanczos")
            vf.extend(_sharpen(crop_w, crop_h, W, H))
        elif bg_mode:
            pre_parts.append(
                f"[0:v]split=2[bgf][fgf];"
                f"[bgf]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"boxblur=40:3,eq=brightness=-0.08[bg];"
                f"[fgf]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos"
                + "".join("," + f for f in _sharpen(source_w, source_h, W, H))
                + "[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1[basev]"
            )
            in_label = "basev"
        else:
            vf.append(f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                      f"crop={W}:{H}:flags=lanczos")
            vf.extend(_sharpen(source_w, source_h, W, H))

    # silence removal after reframing so crop timestamps stay simple
    select_expr = _select_expr(keep_local, duration)
    if select_expr:
        vf.append(f"select='{select_expr}'")
        vf.append("setpts=N/({fps}*TB)".format(fps=FPS))
    vf.append(_subs_filter(ass_path, fonts.font_dir()))

    fc_parts.extend(pre_parts)
    fc_parts.append(f"[{in_label}]" + ",".join(vf) + "[vbase]")
    vlabel = "vbase"

    # 5. audio graph
    has_audio = info.get("has_audio", True)
    audio_map = None
    if has_audio:
        afilters = []
        if select_expr:
            afilters.append(f"aselect='{select_expr}'")
            afilters.append("asetpts=N/SR/TB")
        afilters.append("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo")
        if opts.get("voice_boost", True):
            afilters.append("highpass=f=90")
            afilters.append("acompressor=threshold=-18dB:ratio=3:attack=8:release=180:makeup=2")
        # NOTE: do not use loudnorm here. It buffers with lookahead, which breaks the
        # timing the sidechain ducking depends on. Peak-normalise instead.
        afilters.append("dynaudnorm=f=250:g=15:p=0.9")
        afilters.append("alimiter=limit=0.95")
        fc_parts.append(f"[0:a]{','.join(afilters)}[speech]")
        speech_label = "speech"
        extra_mix: list[str] = []
        if music_index is not None:
            gain = float(opts.get("music_volume") or 0.18)
            fade = float(opts.get("music_fade") or 1.0)
            fc_parts.append(
                f"[{music_index}:a]atrim=0:{clip_duration:.3f},asetpts=N/SR/TB,"
                f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"volume={gain:.2f},afade=t=in:st=0:d={fade:.2f},"
                f"afade=t=out:st={max(0.0, clip_duration - fade):.2f}:d={fade:.2f}[bed]"
            )
            if opts.get("auto_duck", True):
                # The speech stream feeds two filters (the sidechain key and the mix),
                # so it must be split first — a filter output can only be read once.
                fc_parts.append(f"[speech_label]asplit=2[speech_key][speech_mix]".replace(
                    "speech_label", speech_label))
                fc_parts.append(
                    f"[bed][speech_key]sidechaincompress=threshold=0.09:ratio=12:"
                    f"attack=15:release=320:level_sc=6:makeup=1[ducked]"
                )
                extra_mix.append("ducked")
                speech_label = "speech_mix"
            else:
                extra_mix.append("bed")
        for i, (fx_idx, fx) in enumerate(sfx):
            at = max(0.0, min(clip_duration - 0.05, float(fx.get("at") or 0)))
            vol = max(0.0, min(2.0, float(fx.get("volume") or 0.6)))
            fc_parts.append(
                f"[{fx_idx}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"volume={vol:.2f},atrim=0:{max(0.2, float(fx.get('duration') or 3)):.2f},"
                f"asetpts=PTS-STARTPTS,adelay={int(at * 1000)}|{int(at * 1000)}[fx{i}]"
            )
            extra_mix.append(f"fx{i}")
        if extra_mix:
            labels = "".join(f"[{x}]" for x in extra_mix)
            if len(extra_mix) == 1:
                fc_parts.append(f"[{speech_label}][{extra_mix[0]}]amix=inputs=2:duration=first:"
                                f"dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]")
            else:
                fc_parts.append(f"[{speech_label}]{labels}amix=inputs={len(extra_mix) + 1}:"
                                f"duration=first:dropout_transition=0:normalize=0,"
                                f"alimiter=limit=0.95[aout]")
            audio_map = "[aout]"
        else:
            fc_parts.append(f"[{speech_label}]alimiter=limit=0.95[aout]")
            audio_map = "[aout]"

    filter_complex = ";".join(fc_parts)
    cmd += ["-filter_complex", filter_complex, "-map", f"[{vlabel}]"]
    if audio_map:
        cmd += ["-map", audio_map, "-c:a", "aac", "-b:a", spec["audio_bitrate"], "-ar", "48000"]
    else:
        cmd += ["-an"]

    scale = 0.5 if preview else 1.0
    size = f"{media.even(W * scale)}x{media.even(H * scale)}"
    cmd += ["-c:v", "libx264", "-preset", "veryfast" if preview else spec["preset"],
            "-crf", str(24 if preview else spec["crf"]), "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-level", "4.2", "-r", str(FPS),
            "-x264-params", "aq-mode=3:aq-strength=1.0" if not preview else "aq-mode=1",
            "-movflags", "+faststart", "-s", size,
            "-t", f"{clip_duration:.3f}", str(out_path)]

    if on_progress:
        on_progress(0.05)
    media.run(cmd, duration=clip_duration,
              on_progress=(lambda p: on_progress(0.05 + p * 0.9)) if on_progress else None,
              cwd=workdir)
    if on_progress:
        on_progress(1.0)
    return {
        "path": str(out_path),
        "duration": round(clip_duration, 3),
        "width": media.even(W * scale),
        "height": media.even(H * scale),
        "ass": str(ass_path),
        "keep_ranges": keep_local,
        "words": caption_words,
        "aspect": spec["aspect"],
        "platform": spec["platform"],
    }


def render_preview_frame(project: dict, clip: dict, style: CaptionStyle, at: float,
                         out_path: Path, workdir: Path) -> Path:
    """Fast single-frame render for live style preview (no audio, low res)."""
    src = Path(project["video_path"]).resolve()
    info = project.get("media") or media.probe(src)
    clip_start = float(clip["start"])
    clip_end = float(clip["end"])
    duration = max(0.4, clip_end - clip_start)
    opts = project.get("options") or {}
    spec = platforms.resolve(opts)
    W, H = spec["width"], spec["height"]
    style = safe_style(CaptionStyle.from_dict(style.to_dict()), spec)
    fonts.ensure_installed()
    fonts.ensure_font_file(style.font)
    workdir.mkdir(parents=True, exist_ok=True)

    words = clip.get("words_override") or clip.get("words") or []
    local_words = [{**w, "start": max(0, w["start"] - clip_start),
                    "end": max(0, w["end"] - clip_start)} for w in words]
    local_words = [w for w in local_words if w["end"] > 0 and w["start"] < duration]
    if style.emoji:
        local_words = txt.auto_emoji("", local_words, every=style.emoji_every)
    ass_text = build_ass(local_words, style, duration, W, H)
    ass_path = workdir / "preview.ass"
    ass_path.write_text(ass_text, encoding="utf-8")

    vf = ["setsar=1"]
    source_w, source_h = info["width"], info["height"]
    crop_plan = clip.get("crop_plan") or {}
    if opts.get("blur_background"):
        vf.append(
            f"split=2[bgf][fgf];[bgf]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},boxblur=40:3,eq=brightness=-0.08[bg];"
            f"[fgf]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )
    elif crop_plan.get("crop"):
        cw = int(crop_plan["crop"]["w"])
        ch = int(crop_plan["crop"]["h"])
        kfs = crop_plan.get("keyframes") or []
        cx, cy = (kfs[0][1], kfs[0][2]) if kfs else (source_w / 2, source_h / 2)
        x = max(0, min(source_w - cw, cx - cw / 2))
        y = max(0, min(source_h - ch, cy - ch / 2))
        vf.append(f"crop=w={cw}:h={ch}:x={x:.0f}:y={y:.0f}")
        vf.append(f"scale={W}:{H}:flags=lanczos")
    else:
        vf.append(f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}")
    vf.append(_subs_filter(ass_path, fonts.font_dir()))
    vf_str = ",".join(vf)
    small_w = 540
    small_h = media.even(small_w * H / W)
    cmd = ["-y", "-ss", f"{clip_start + at:.3f}", "-i", str(src),
           "-vf", vf_str, "-frames:v", "1", "-q:v", "3",
           "-s", f"{small_w}x{small_h}", str(out_path)]
    media.run(cmd, cwd=workdir)
    return out_path
