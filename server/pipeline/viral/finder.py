"""The viral-clip pipeline, stage by stage.

Design rules that make the rest of the app simple:

  * every stage writes a file under data/viral/<video_id>/ and, if that file
    already exists, the stage is skipped. Re-running a job is cheap and closing
    the tab loses nothing.
  * the cheap stages run first, and video bytes are downloaded only for the
    finalists — never the whole source.
  * a missing signal is not an error; it is recorded as `null` so the scorer and
    the UI can say which signals were used.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import time
from pathlib import Path

from ... import config, jobs
from .. import downloader, ytlink
from . import judge, scoring, signals

WARN_SECONDS = 3 * 3600


class Blocked(RuntimeError):
    """The source cannot be analysed. Carries the upload-fallback advice."""

    def __init__(self, message: str, reason: str = "blocked", hint: str = ""):
        super().__init__(message)
        self.reason = reason
        self.hint = hint

    def as_dict(self) -> dict:
        return {"message": str(self), "reason": self.reason, "hint": self.hint}


def cache_dir(video_id: str) -> Path:
    path = config.DATA / "viral" / video_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cached(video_id: str, name: str) -> dict | None:
    path = cache_dir(video_id) / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _store(video_id: str, name: str, data: dict) -> dict:
    path = cache_dir(video_id) / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


# --------------------------------------------------------------- stage 0 ----


def prepare(url: str, overrides: dict | None = None) -> dict:
    """Normalise the link and fetch metadata. Never downloads video.

    Returns the info dict plus `parsed`, and raises Blocked with a friendly
    message for private/upcoming/unavailable sources.
    """
    parsed = ytlink.parse(url)
    video_id = parsed["video_id"]
    info = _cached(video_id, "info.json")
    if info:
        info["cached"] = True
        return {**info, "parsed": parsed}
    try:
        info = downloader.metadata(parsed["canonical"])
    except downloader.DownloadError as exc:
        reasons = {"private", "members_only", "age_restricted", "region",
                   "unavailable", "removed", "bot_check"}
        if exc.reason in reasons:
            raise Blocked(str(exc), exc.reason, exc.hint) from exc
        raise
    info["parsed"] = parsed
    _store(video_id, "info.json", info)
    return info


def check_source(info: dict, allow_partial_live: bool = False) -> None:
    """Reject states that have nothing to analyse yet, with a way forward."""
    state = info.get("state")
    if state == "upcoming":
        raise Blocked(
            "This premiere has not started yet, so there is nothing to analyse.",
            "upcoming",
            "Come back when it starts, or upload the file after it airs.",
        )
    if state == "live" and not allow_partial_live:
        raise Blocked(
            "This stream is live right now. Only the part that already aired can be analysed.",
            "live",
            "Tick “Analyze what has been streamed so far” to work with the aired portion.",
        )


# ------------------------------------------------------------ transcripts --


_TIME_TAG = re.compile(r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})")
_WORD_TAG = re.compile(r"<(\d{2}):(\d{2}):(\d{2})\.(\d{3})>")


def parse_vtt(path: Path) -> list[dict]:
    """YouTube VTT → word list, using per-word timing when present.

    Auto-generated captions carry inline <hh:mm:ss.mmm> tags per word; manual
    captions only have cue timing, in which case words are spread across the cue.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    words: list[dict] = []
    blocks = re.split(r"\n\s*\n", raw)
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip() and not ln.startswith("WEBVTT")]
        if not lines:
            continue
        m = _TIME_TAG.search(lines[0])
        if not m:
            continue
        cue_start = _secs(*m.groups()[:4])
        cue_end = _secs(*m.groups()[4:8])
        body = " ".join(lines[1:])
        body = re.sub(r"<c(?:\.[^>]*)?>|</c>", "", body)
        stamps = list(_WORD_TAG.finditer(body))
        if stamps:
            plain = _WORD_TAG.sub("", body)
            parts = plain.split()
            # a stamp precedes each word except the first
            bounds = [cue_start] + [_secs(*s.groups()) for s in stamps]
            bounds = bounds[: len(parts)]
            for i, text in enumerate(parts):
                start = bounds[i] if i < len(bounds) else cue_start
                end = bounds[i + 1] if i + 1 < len(bounds) else cue_end
                words.append({"text": clean(text), "start": round(start, 3), "end": round(end, 3)})
        else:
            parts = body.split()
            if not parts:
                continue
            step = max(0.05, (cue_end - cue_start) / len(parts))
            for i, text in enumerate(parts):
                words.append({
                    "text": clean(text),
                    "start": round(cue_start + i * step, 3),
                    "end": round(cue_start + (i + 1) * step, 3),
                })
    return [w for w in words if w["text"]]


def _secs(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
    text = text.replace("&gt;", ">").replace("&lt;", "<").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def _pick_lang(langs: list[str]) -> str:
    for want in ("en", "en-US", "en-GB"):
        if want in langs:
            return want
    for lang in langs:
        if lang.startswith("en"):
            return lang
    return langs[0] if langs else "en"


def transcript(info: dict, workdir: Path, on_stage) -> dict:
    """YouTube captions first (free and fast), Whisper only as a fallback."""
    video_id = info["video_id"]
    cached = _cached(video_id, "transcript.json")
    if cached:
        return cached

    langs = info.get("sub_langs") or []
    if langs:
        on_stage("Transcribing", "Reading the creator's captions…")
        code = _pick_lang(langs)
        for auto in (False, True):
            found = downloader.download_subs(
                info["url"] or ytlink.watch_url(video_id), code, workdir / "subs", auto=auto
            )
            if found:
                path = found.get(code) or next(iter(found.values()))
                words = parse_vtt(path)
                if len(words) > 20:
                    return _store(video_id, "transcript.json", {
                        "words": words,
                        "source": "youtube",
                        "language": code,
                        "note": "Creator captions, so no speech model was needed.",
                    })

    # fall back to audio + Whisper
    on_stage("Transcribing", "No captions available — fetching audio for Whisper…")
    audio = workdir / "audio.wav"
    try:
        downloader.download_audio(info["url"] or ytlink.watch_url(video_id), audio,
                                  on_progress=lambda p, m: on_stage("Transcribing", m))
    except downloader.DownloadError as exc:
        raise Blocked(str(exc), exc.reason, exc.hint) from exc
    return whisper_transcript(video_id, audio, on_stage)


def whisper_transcript(video_id: str, audio: Path, on_stage) -> dict:
    from .. import transcribe

    on_stage("Transcribing", "Running Whisper on the audio…")

    def prog(p: float, msg: str) -> None:
        on_stage("Transcribing", f"{msg} {p * 100:.0f}%")

    result = transcribe.transcribe(audio, on_progress=prog)
    words = result.get("words") or []
    return _store(video_id, "transcript.json", {
        "words": words,
        "source": "whisper",
        "language": result.get("language") or "en",
        "note": "Transcribed locally with Whisper.",
    })


# --------------------------------------------------------------- signals --


def collect_signals(info: dict, workdir: Path, on_stage) -> dict:
    """Heatmap, chat and audio. Each is optional and cached separately."""
    video_id = info["video_id"]
    cached = _cached(video_id, "signals.json")
    if cached:
        return cached

    duration = float(info.get("duration") or 0)

    heat = None
    if info.get("heatmap"):
        curve = signals.heatmap_curve(info["heatmap"], duration)
        if curve:
            heat = {"peaks": signals.heatmap_peaks(info["heatmap"], duration), "curve": curve}

    on_stage("Reading chat and replays", "Checking for a chat replay…")
    chat = None
    if info.get("state") == "ended_live":
        chat_path = downloader.list_chat(info["url"] or ytlink.watch_url(video_id), workdir / "chat")
        if chat_path:
            messages = signals.parse_chat(chat_path)
            spikes = signals.chat_spikes(messages, duration)
            if spikes:
                chat = {"messages": len(messages), "peaks": spikes}

    on_stage("Listening", "Measuring loudness across the source…")
    audio = None
    audio_path = workdir / "envelope.wav"
    try:
        downloader.download_audio(info["url"] or ytlink.watch_url(video_id), audio_path,
                                  on_progress=lambda p, m: on_stage("Listening", m))
        envelope = signals.audio_envelope(audio_path)
        if envelope:
            peaks = signals.audio_spikes(envelope)
            if peaks:
                audio = {"peaks": peaks, "bucket": 1.0}
    except downloader.DownloadError:
        audio = None
    finally:
        shutil.rmtree(workdir / "audio-tmp", ignore_errors=True)

    return _store(video_id, "signals.json", {"heatmap": heat, "chat": chat, "audio": audio})


def _stamp(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def stamped(transcript_words: list[dict], window: float = 480, overlap: float = 60) -> list[str]:
    """Split into ~8 min windows with 1 min overlap, each line time-stamped."""
    if not transcript_words:
        return []
    total = transcript_words[-1]["end"]
    out = []
    start = 0.0
    while start < total:
        end = min(total, start + window)
        lines = []
        last_second = -1
        for w in transcript_words:
            if w["end"] <= start or w["start"] >= end:
                continue
            second = int(w["start"])
            if second != last_second:
                lines.append(f"\n[{_stamp(w['start'])}]")
                last_second = second
            lines.append(w["text"])
        out.append(" ".join(lines).replace(" \n", "\n").strip())
        if end >= total:
            break
        start += window - overlap
    return out


# ------------------------------------------------------------- the stages --


def _opts_key(opts: dict) -> str:
    """Cache suffix: the same video with different options is a different search."""
    import hashlib

    raw = json.dumps({k: opts.get(k) for k in (
        "count", "min_len", "max_len", "target_len", "content_type",
        "topics", "range_start", "range_end",
    )}, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def build_candidates(info: dict, sig: dict, transcript_data: dict, opts: dict, on_stage) -> list[dict]:
    """Stage B: signals + an LLM scan, merged into 15–30 windows."""
    video_id = info["video_id"]
    cache_name = f"candidates-{_opts_key(opts)}.json"
    cached = _cached(video_id, cache_name)
    if cached:
        return cached["candidates"]

    total = float(info.get("duration") or 0)
    min_len = float(opts.get("min_len") or 20)
    max_len = float(opts.get("max_len") or 60)
    target = float(opts.get("target_len") or 35)

    pool: list[dict] = []
    if sig.get("heatmap"):
        pool += scoring.from_peaks(sig["heatmap"]["peaks"], target, min_len, max_len, total)
    if sig.get("chat"):
        pool += scoring.from_peaks(sig["chat"]["peaks"], target, min_len, max_len, total)
    if sig.get("audio"):
        pool += scoring.from_peaks(sig["audio"]["peaks"], target, min_len, max_len, total)

    words = transcript_data.get("words") or []
    on_stage("Finding moments", "Reading the transcript for hooky moments…")
    windows = stamped(words)
    for i, text in enumerate(windows):
        found = judge.find_moments(
            text, total, opts, count=max(4, int(opts.get("count") or 5)),
            window_note=f"window {i + 1} of {len(windows)}",
        )
        pool += found

    if not pool and words:
        # no signals and no model: even then, offer evenly spaced sentences
        span = total / max(1, int(opts.get("count") or 5))
        for i in range(int(opts.get("count") or 5)):
            pool.append({"start": i * span, "end": min(total, i * span + target), "origin": "fallback"})

    lo = float(opts.get("range_start") or 0)
    hi = float(opts.get("range_end") or 0)
    if hi > lo:
        pool = [c for c in pool if c["end"] > lo and c["start"] < hi]

    merged = scoring.merge(pool)
    # keep the pool a sane size for the judge
    merged = sorted(merged, key=lambda c: -len(c.get("origins", [])))[:30]
    _store(video_id, cache_name, {"candidates": merged, "windows": len(windows)})
    return merged


def _signal_value(sig: dict, key: str, start: float, end: float) -> float | None:
    """Signal strength over a window, 0..1, or None if the signal is absent."""
    data = sig.get(key)
    if not data:
        return None
    if key == "heatmap":
        curve = data.get("curve") or []
        lo, hi = int(start), min(len(curve), int(math.ceil(end)))
        if lo >= hi:
            return 0.0
        return round(sum(curve[lo:hi]) / (hi - lo), 4)
    peaks = data.get("peaks") or []
    best = 0.0
    for peak in peaks:
        if start - 5 <= peak["at"] <= end + 5:
            best = max(best, float(peak.get("strength") or 0))
    # normalise: z=6 or a +18dB jump is already a very strong moment
    cap = 6.0 if key == "chat" else 18.0
    return round(min(1.0, best / cap), 4)


def score_and_select(info: dict, sig: dict, transcript_data: dict, candidates: list[dict],
                     opts: dict, on_stage) -> list[dict]:
    """Stage C+D: snap boundaries, judge, blend, pick the top N."""
    video_id = info["video_id"]
    cache_name = f"ranked-{_opts_key(opts)}.json"
    cached = _cached(video_id, cache_name)
    if cached and not opts.get("exclude"):
        return cached["clips"]

    words = transcript_data.get("words") or []
    total = float(info.get("duration") or 0)
    min_len = float(opts.get("min_len") or 20)
    max_len = float(opts.get("max_len") or 60)

    on_stage("Finding moments", "Snapping cuts to sentence boundaries…")
    snapped = [scoring.snap(c, words, min_len, max_len, total) for c in candidates]
    snapped = [c for c in snapped if c["end"] - c["start"] >= min(min_len, 5)]

    for cand in snapped:
        cand["signals"] = {
            key: _signal_value(sig, key, cand["start"], cand["end"])
            for key in ("heatmap", "chat", "audio")
        }

    on_stage("Scoring", "Scoring the candidates…")
    judged = judge.judge_candidates(snapped, opts)

    cfg = scoring.load_config()
    for cand in judged:
        blend = scoring.blend(cand, cfg)
        cand["total"] = blend["total"]
        cand["score_parts"] = blend["parts"]
        cand["signals_used"] = blend["used"]

    want = int(opts.get("count") or 5)
    exclude = [(float(a), float(b)) for a, b in (opts.get("exclude") or [])]

    def already_have(cand: dict) -> bool:
        """Find more must not hand back a clip the user already has."""
        for a, b in exclude:
            inter = min(cand["end"], b) - max(cand["start"], a)
            if inter > 0:
                return True
        return False

    chosen = scoring.select([c for c in judged if not already_have(c)], want, min_gap=30)
    for cand in chosen:
        finalise_meta(cand, opts, info)

    shortfall = ""
    if len(chosen) < want:
        shortfall = (
            f"Only {len(chosen)} clip{'s' if len(chosen) != 1 else ''} met the quality bar"
            + ("." if not chosen else f" ({want} requested).")
        )
        if total < want * min_len:
            shortfall += (
                f" The source is {_stamp(total)} long, so there is not enough material"
                f" for {want} clips of {int(min_len)}–{int(max_len)}s."
            )

    _store(video_id, cache_name, {
        "clips": chosen,
        "shortfall": shortfall,
        "requested": want,
        "candidates": len(judged),
    })
    return chosen


def finalise_meta(cand: dict, opts: dict, info: dict) -> None:
    """Stage E: title, hook, hashtags and the signal chips."""
    fallback = _topic_tags(opts.get("topics") or "", info.get("channel") or "")
    cand["title"] = scoring.tidy_title(cand.get("title") or _title_from(cand))
    cand["hook_line"] = (cand.get("hook_line") or cand["title"])[:120]
    cand["hashtags"] = scoring.tidy_hashtags(cand.get("hashtags"), fallback)
    cand["duration"] = round(cand["end"] - cand["start"], 2)
    cand["watch_url"] = ytlink.watch_url(info["video_id"], cand["start"])
    cand["best_platform"] = cand.get("best_platform") or "reels"
    cand["signals_used"] = cand.get("signals_used") or []
    cand["signal_chips"] = _chips(cand)
    cand["score_total"] = cand.get("total") or 0
    cand["status"] = cand.get("status") or "new"


def _title_from(cand: dict) -> str:
    text = (cand.get("text") or "").strip()
    return (re.split(r"(?<=[.!?])\s", text)[0] if text else "Clip")[:60]


def _topic_tags(topics: str, channel: str) -> list[str]:
    tags = []
    for part in re.split(r"[,;/]", topics or ""):
        if part.strip():
            tags.append(part.strip())
    if channel:
        tags.extend(channel.split()[:2])
    tags += ["shorts", "viral", "clips"]
    return tags


def _chips(cand: dict) -> list[str]:
    chips = []
    parts = cand.get("score_parts") or {}
    used = cand.get("signals_used") or []
    if "heatmap" in used and (cand.get("signals") or {}).get("heatmap"):
        chips.append("Most replayed")
    if "chat" in used and (cand.get("signals") or {}).get("chat"):
        chips.append("Chat spike")
    if "audio" in used and (cand.get("signals") or {}).get("audio"):
        chips.append("Loud moment")
    if (cand.get("scores") or {}).get("emotion", 0) >= 70:
        chips.append("Laughter / reaction")
    if parts.get("llm") is not None:
        chips.append("Judge scored")
    return chips


# -------------------------------------------------------------- the job ----


def run_job(job_id: str) -> None:
    """The whole pipeline for one job. Every step is safe to repeat."""
    job = jobs.get(job_id)
    if not job:
        return
    payload = job.get("payload") or {}
    url = payload.get("url") or ""

    def stage(name: str, message: str, progress: float | None = None) -> None:
        jobs.update(job_id, progress if progress is not None else _stage_progress(name),
                    name, message)

    try:
        jobs.update(job_id, 0.02, "Fetching", "Reading the link…")
        info = prepare(url, payload.get("options"))
        jobs.patch(job_id, video_id=info["video_id"], info=_public_info(info))
        check_source(info, allow_partial_live=bool(payload.get("allow_partial_live")))
        if info.get("state") == "live" and payload.get("allow_partial_live"):
            # analyse only what aired: the recording is capped, then treated as
            # a normal finished file
            info = record_partial_live(info, job_id, payload.get("cap_seconds") or 1800, stage)

        workdir = config.JOBS / job_id / "viral"
        workdir.mkdir(parents=True, exist_ok=True)

        transcript_data = transcript(info, workdir, stage)
        jobs.patch(job_id, transcript_source=transcript_data.get("source"),
                   word_count=len(transcript_data.get("words") or []))
        sig = collect_signals(info, workdir, stage)
        jobs.patch(job_id, signals_available=_public_signals(sig))

        opts = _options(payload, info)
        candidates = build_candidates(info, sig, transcript_data, opts, stage)
        clips = score_and_select(info, sig, transcript_data, candidates, opts, stage)

        result = _cached(info["video_id"], "ranked.json") or {}
        jobs.patch(
            job_id,
            clips=clips,
            clip_count=len(clips),
            shortfall=result.get("shortfall") or "",
            info=_public_info(info),
        )
        jobs.update(job_id, 1.0, "Done",
                    f"{len(clips)} clip{'s' if len(clips) != 1 else ''} ready.")
    except Blocked as exc:
        jobs.patch(job_id, error=exc.as_dict())
        jobs.update(job_id, 1.0, "Blocked", str(exc))
        jobs.set_status(job_id, "error")
    except downloader.DownloadError as exc:
        jobs.patch(job_id, error=exc.as_dict())
        jobs.update(job_id, 1.0, "Failed", str(exc))
        jobs.set_status(job_id, "error")
    except Exception as exc:  # noqa: BLE001 - job runner must never crash silently
        jobs.patch(job_id, error={"message": f"{type(exc).__name__}: {exc}",
                                  "reason": "internal", "hint": ""})
        jobs.update(job_id, 1.0, "Failed", f"Something went wrong: {exc}")
        jobs.set_status(job_id, "error")


def _stage_progress(name: str) -> float:
    return {
        "Fetching": 0.08,
        "Transcribing": 0.28,
        "Reading chat and replays": 0.45,
        "Listening": 0.52,
        "Finding moments": 0.7,
        "Scoring": 0.86,
        "Matching voice": 0.9,
    }.get(name, 0.5)


def record_partial_live(info: dict, job_id: str, cap_seconds: int, stage) -> dict:
    """Record the aired part of a live stream, then continue as normal."""
    stage("Fetching", f"Recording up to {cap_seconds // 60} minutes of the stream…")
    out = config.JOBS / job_id / "live.mp4"
    try:
        downloader.record_live(info["url"], out, cap_seconds=cap_seconds,
                              on_progress=lambda p, m: stage("Fetching", m, 0.05 + p * 0.1))
    except downloader.DownloadError as exc:
        raise Blocked(str(exc), exc.reason, exc.hint) from exc
    from .. import media

    probed = media.probe(out)
    return {
        **info,
        "state": "ended_live",
        "duration": probed["duration"],
        "local_path": str(out),
        "note": "Analysed the portion of the stream that had already aired.",
    }


def _options(payload: dict, info: dict) -> dict:
    raw = payload.get("options") or {}
    count = int(raw.get("count") or 5)
    count = max(3, min(10, count))
    min_len = float(raw.get("min_len") or 20)
    max_len = float(raw.get("max_len") or 60)
    if max_len < min_len:
        max_len = min_len + 20
    start = float(raw.get("range_start") or 0)
    end = float(raw.get("range_end") or 0)
    return {
        "count": count,
        "min_len": min_len,
        "max_len": max_len,
        "target_len": min(max_len, max(min_len, 35)),
        "content_type": raw.get("content_type") or "general",
        "topics": (raw.get("topics") or "")[:200],
        "range_start": start,
        "range_end": end,
        "credit": (raw.get("credit") or "")[:200],
    }


def _public_info(info: dict) -> dict:
    return {
        key: info.get(key) for key in (
            "video_id", "title", "channel", "duration", "state", "thumbnail",
            "chapters", "view_count", "upload_date", "note", "local_path",
        )
    }


def _public_signals(sig: dict) -> dict:
    out = {}
    for key in ("heatmap", "chat", "audio"):
        data = sig.get(key)
        if not data:
            out[key] = None
            continue
        out[key] = {
            "peaks": len(data.get("peaks") or []),
            "messages": data.get("messages"),
        }
    return out


def estimate(url: str) -> dict:
    """Cost/time preview from metadata alone, before enqueuing a job."""
    info = prepare(url)
    minutes = (info.get("duration") or 0) / 60
    transcript_data = _cached(info["video_id"], "transcript.json") or {}
    words = transcript_data.get("words") or []
    text = " ".join(w.get("text", "") for w in words)
    est = judge.estimate(text or " " * int(minutes * 900), {}, minutes)
    est.update({
        "title": info.get("title"),
        "channel": info.get("channel"),
        "duration": info.get("duration"),
        "state": info.get("state"),
        "long": (info.get("duration") or 0) > WARN_SECONDS,
        "heatmap": bool(info.get("heatmap")),
        "subs": bool(info.get("sub_langs")),
        "cached": bool(_cached(info["video_id"], "ranked.json")),
    })
    return est
