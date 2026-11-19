#!/usr/bin/env python3
"""End-to-end smoke test using the bundled sample video.

    PYTHONPATH=. .venv/bin/python scripts/demo.py [--clips 2] [--preset karaoke]

Runs the exact same pipeline the UI uses: probe -> extract audio -> transcribe ->
detect clips -> face-track -> render MP4. Useful for checking an install without
opening a browser.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config, library, store  # noqa: E402
from server.pipeline import llm, media, reframe, render, text as txt  # noqa: E402
from server.pipeline import transcribe as transcribe_mod  # noqa: E402
from server.pipeline.captions.presets import style_for  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video", default=str(config.SAMPLE_DIR / "demo-720p.mp4"))
    ap.add_argument("--clips", type=int, default=2)
    ap.add_argument("--preset", default="bold_impact")
    ap.add_argument("--out", default=str(config.DATA / "demo-output"))
    args = ap.parse_args()

    src = Path(args.video).resolve()
    if not src.exists():
        print(f"Sample video not found: {src}")
        print("Upload your own instead, or regenerate the sample with:")
        print("  ffmpeg -f lavfi -i testsrc2=s=1280x720:d=30 -f lavfi -i sine=f=220:d=30 \\")
        print("         -shortest -c:v libx264 -c:a aac assets/samples/demo-720p.mp4")
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"▸ Probing {src.name}")
    info = media.probe(src)
    print(f"  {info['width']}x{info['height']} · {info['duration']:.1f}s · {info['fps']}fps")

    project = library.new_project("Demo", "sample", src, info)
    print(f"▸ Project {project['id']}")

    wav = library.project_dir(project["id"]) / "audio.wav"
    print("▸ Extracting audio")
    media.extract_audio(src, wav, duration=info["duration"])

    print("▸ Transcribing (first run downloads the model)")
    t0 = time.time()
    transcript = transcribe_mod.transcribe(wav, duration=info["duration"])
    print(f"  {len(transcript['words'])} words · {transcript['language']} · {time.time() - t0:.1f}s")

    print("▸ Detecting clips")
    clips, provider = llm.detect_clips(
        transcript["words"], count=args.clips, settings=store.load_settings()
    )
    print(f"  provider: {provider}")
    if not clips:
        print("  No clips found — try a longer video.")
        return 1

    style = style_for(args.preset).to_dict()
    project["options"].update({"remove_silences": True, "remove_fillers": True})

    for i, clip in enumerate(clips, 1):
        clip["id"] = f"c{i}"
        clip["style"] = style
        clip["words"] = txt.words_between(transcript["words"], clip["start"], clip["end"])
        print(f"▸ Clip {i}: {clip['title'][:60]}")
        print(f"  {clip['start']:.1f}s → {clip['end']:.1f}s · score {clip['score']}")
        try:
            clip["crop_plan"] = reframe.analyze(src, clip["start"], clip["end"],
                                                info["width"], info["height"])
            print(f"  faces: {clip['crop_plan']['faces_found']} samples")
        except Exception as exc:
            print(f"  face tracking unavailable ({exc}); using centre crop")
            clip["crop_plan"] = reframe.still_crop(info["width"], info["height"])

        stem = "".join(c if c.isalnum() else "-" for c in clip["title"][:40]).strip("-").lower()
        out = out_dir / f"{i:02d}-{stem or 'clip'}.mp4"
        t0 = time.time()
        result = render.render_clip(project, clip, out, out_dir / f"work-{i}")
        print(f"  → {out}  ({result['duration']:.1f}s, {out.stat().st_size // 1024} KB, "
              f"{time.time() - t0:.1f}s to render)")

    print("\nDone. Open the files in", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
