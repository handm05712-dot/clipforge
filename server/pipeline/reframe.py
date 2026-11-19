"""Auto-reframe: face tracking with MediaPipe, smoothed 9:16 crop path.

Produces a per-clip JSON crop plan:
    {"fps": 12, "keyframes": [[t_local, cx, cy], ...],
     "wide": bool, "source": {"width": w, "height": h}}
The renderer turns this into an animated crop with sendcmd.
"""
from __future__ import annotations

import math
import urllib.request
from pathlib import Path
from typing import Callable

from .. import config

DetectorFactory = None


def _ensure_model(download: bool = True) -> Path | None:
    if config.FACE_MODEL.exists():
        return config.FACE_MODEL
    if not download:
        return None
    try:
        config.FACE_MODEL.parent.mkdir(parents=True, exist_ok=True)
        tmp = config.FACE_MODEL.with_suffix(".part")
        urllib.request.urlretrieve(config.FACE_MODEL_URL, tmp)
        tmp.replace(config.FACE_MODEL)
        return config.FACE_MODEL
    except Exception as exc:
        print(f"[faces] model download failed: {exc}")
        return None


def _detector(score: float = 0.35):
    import mediapipe as mp

    model_path = _ensure_model()
    if not model_path:
        return None
    base = mp.tasks.BaseOptions(model_asset_path=str(model_path))
    options = mp.tasks.vision.FaceDetectorOptions(base_options=base, min_detection_confidence=score,
                                                  running_mode=mp.tasks.vision.RunningMode.IMAGE)
    return mp.tasks.vision.FaceDetector.create_from_options(options)


def _smooth(values: list[float], window: int = 9) -> list[float]:
    if not values:
        return []
    out = []
    half = max(1, window // 2)
    for i in range(len(values)):
        chunk = values[max(0, i - half): i + half + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def analyze(src: Path, start: float, end: float, source_w: int, source_h: int,
            on_progress: Callable[[float], None] | None = None,
            sample_fps: float = 6.0) -> dict:
    """Sample frames in [start, end] and return a smoothed crop plan in source pixels."""
    import cv2

    plan = {
        "fps": 12,
        "keyframes": [],
        "wide": False,
        "faces_found": 0,
        "source": {"width": source_w, "height": source_h},
    }
    # 9:16 crop box (use source height if it is the limiting dimension)
    crop_h = min(source_h, source_w * 16 / 9)
    crop_w = crop_h * 9 / 16
    crop_w = min(crop_w, source_w)
    crop_h = min(source_h, crop_w * 16 / 9)
    plan["crop"] = {"w": round(crop_w, 2), "h": round(crop_h, 2)}

    detector = None
    try:
        detector = _detector()
    except Exception as exc:
        print(f"[faces] detector unavailable: {exc}")

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return plan
    duration = max(0.2, end - start)
    n = max(2, int(duration * sample_fps))
    times = [start + (duration * i / max(1, n - 1)) for i in range(n)]
    xs: list[float | None] = []
    ys: list[float | None] = []
    sizes: list[float | None] = []
    import mediapipe as mp

    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok or frame is None:
            xs.append(None); ys.append(None); sizes.append(None)
            continue
        frame_w = frame.shape[1]
        frame_h = frame.shape[0]
        fx, fy, fw = None, None, None
        if detector is not None:
            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = detector.detect(mp_img)
                boxes = getattr(result, "detections", []) or []
                if boxes:
                    best = max(boxes, key=lambda d: d.bounding_box.width * d.bounding_box.height)
                    bb = best.bounding_box
                    fx = (bb.origin_x + bb.width / 2) / frame_w
                    fy = (bb.origin_y + bb.height / 2) / frame_h
                    fw = bb.width / frame_w
            except Exception:
                pass
        # expand normalized coords to source aspect (frames are already source-sized)
        xs.append(None if fx is None else fx * frame_w)
        ys.append(None if fy is None else fy * frame_h)
        sizes.append(None if fw is None else fw * frame_w)
    cap.release()
    if detector is not None:
        try:
            detector.close()
        except Exception:
            pass

    found = sum(1 for x in xs if x is not None)
    plan["faces_found"] = found
    plan["wide"] = found < max(1, len(xs) // 3)

    # fill gaps by interpolation, default to centre
    def fill(seq: list[float | None], default: float) -> list[float]:
        out: list[float | None] = list(seq)
        known = [i for i, v in enumerate(out) if v is not None]
        if not known:
            return [default] * len(out)
        for i, v in enumerate(out):
            if v is not None:
                continue
            prev = max((k for k in known if k < i), default=None)
            nxt = min((k for k in known if k > i), default=None)
            if prev is None:
                out[i] = out[nxt]  # type: ignore[index]
            elif nxt is None:
                out[i] = out[prev]  # type: ignore[index]
            else:
                a, b = out[prev], out[nxt]  # type: ignore[index]
                w = (i - prev) / (nxt - prev)
                out[i] = a + (b - a) * w  # type: ignore[operator]
        return [float(v) for v in out]  # type: ignore[arg-type]

    fxvals = _smooth(fill(xs, source_w / 2), 7)
    fyvals = _smooth(fill(ys, source_h * 0.38), 7)
    # Keep the frame centre in view
    half_w = crop_w / 2
    half_h = crop_h / 2
    keyframes = []
    for i, t in enumerate(times):
        cx = min(max(fxvals[i], half_w), max(half_w, source_w - half_w))
        cy = min(max(fyvals[i], half_h), max(half_h, source_h - half_h))
        keyframes.append([round(t - start, 3), round(cx, 2), round(cy, 2)])
    plan["keyframes"] = keyframes
    if on_progress:
        on_progress(1.0)
    return plan


def still_crop(source_w: int, source_h: int) -> dict:
    crop_h = min(source_h, source_w * 16 / 9)
    crop_w = crop_h * 9 / 16
    return {"fps": 12, "keyframes": [[0, source_w / 2, source_h / 2]], "wide": False,
            "faces_found": 0, "source": {"width": source_w, "height": source_h},
            "crop": {"w": round(crop_w, 2), "h": round(crop_h, 2)}}
