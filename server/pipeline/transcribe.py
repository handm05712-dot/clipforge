"""Word-level transcription with faster-whisper (cached model, CPU friendly)."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from .. import store

_MODEL_CACHE: dict = {}
_MODEL_LOCK = threading.Lock()


def _model_root() -> str:
    """Keep downloaded Whisper models with the rest of the app data.

    On a container the data dir is a volume, so the model downloads once and
    survives a redeploy instead of being fetched into a throwaway home dir.
    """
    import os

    from .. import config

    root = os.environ.get("WHISPER_CACHE") or str(config.MODELS / "whisper")
    Path(root).mkdir(parents=True, exist_ok=True)
    return root


def get_model(name: str):
    with _MODEL_LOCK:
        if name not in _MODEL_CACHE:
            from faster_whisper import WhisperModel

            # int8 on CPU keeps base/small usable on small VMs
            _MODEL_CACHE[name] = WhisperModel(name, device="cpu", compute_type="int8",
                                              download_root=_model_root())
        return _MODEL_CACHE[name]


def transcribe(audio_path: Path, on_progress: Callable[[float, str], None] | None = None,
               duration: float | None = None) -> dict:
    settings = store.load_settings()
    model_name = settings.get("whisper_model") or "base"
    language = settings.get("language") or "auto"
    if on_progress:
        on_progress(0.02, f"Loading Whisper model ({model_name})…")
    model = get_model(model_name)
    if on_progress:
        on_progress(0.10, "Transcribing speech…")

    segments_iter, info = model.transcribe(
        str(audio_path),
        language=None if language == "auto" else language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        beam_size=5,
        condition_on_previous_text=False,
    )

    total = float(getattr(info, "duration", 0) or duration or 0) or 1.0
    segments: list[dict] = []
    words: list[dict] = []
    for seg in segments_iter:
        seg_words = []
        for w in (seg.words or []):
            text = (w.word or "").strip()
            if not text:
                continue
            item = {"text": text, "start": round(float(w.start), 3),
                    "end": round(float(w.end), 3), "prob": round(float(getattr(w, "probability", 0) or 0), 3)}
            words.append(item)
            seg_words.append(item)
        segments.append({
            "id": len(segments),
            "start": round(float(seg.start), 3),
            "end": round(float(seg.end), 3),
            "text": (seg.text or "").strip(),
            "words": seg_words,
        })
        if on_progress and total:
            on_progress(min(0.85, 0.10 + 0.75 * (float(seg.end) / total)),
                        f"Transcribing… {int(float(seg.end))}s / {int(total)}s")

    words.sort(key=lambda w: w["start"])
    if on_progress:
        on_progress(0.9, "Building word timeline…")
    return {
        "language": getattr(info, "language", None) or "unknown",
        "language_probability": round(float(getattr(info, "language_probability", 0) or 0), 3),
        "duration": round(total, 3),
        "segments": segments,
        "words": words,
        "text": " ".join(s["text"] for s in segments).strip(),
    }
