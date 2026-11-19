import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../state";
import { fmt, fmtPrecise, useLocalWords } from "../lib";
import type { CaptionStyle, Clip, CropPlan, Project } from "../types";
import { CaptionOverlay } from "./CaptionOverlay";
import { Timeline } from "./Timeline";

type Props = {
  project: Project;
  clip: Clip;
  style: CaptionStyle;
  onClipChange: (patch: Partial<Clip>) => void;
  onSaveStyle?: (style: CaptionStyle) => void;
};

const MAX_PHONE_W = 330;

/** Phones in the editor are small; fullscreen should use the whole viewport. */
const FULLSCREEN_W = 560;

/** Render the canvas at device resolution so enlarging it stays sharp. */
const DPR = Math.min(2, typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1);

/** Centre of the crop window at a clip-local time, interpolated from keyframes. */
export function cropCentre(plan: CropPlan | null | undefined, t: number) {
  if (!plan?.keyframes?.length) return null;
  const kfs = plan.keyframes;
  if (t <= kfs[0][0]) return { x: kfs[0][1], y: kfs[0][2] };
  for (let i = 1; i < kfs.length; i++) {
    if (t <= kfs[i][0]) {
      const [t0, x0, y0] = kfs[i - 1];
      const [t1, x1, y1] = kfs[i];
      const f = t1 - t0 <= 0.001 ? 0 : (t - t0) / (t1 - t0);
      return { x: x0 + (x1 - x0) * f, y: y0 + (y1 - y0) * f };
    }
  }
  const last = kfs[kfs.length - 1];
  return { x: last[1], y: last[2] };
}

export function PreviewColumn({ project, clip, style, onClipChange, onSaveStyle }: Props) {
  const app = useApp();
  const rawWords = useLocalWords(clip);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [shot, setShot] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [shotError, setShotError] = useState<string | null>(null);
  const [showText, setShowText] = useState(false);
  const [videoReady, setVideoReady] = useState(false);
  const [videoError, setVideoError] = useState(false);
  const [draft, setDraft] = useState("");
  const [showSafe, setShowSafe] = useState(false);
  const [full, setFull] = useState(false);
  const [viewport, setViewport] = useState({ w: window.innerWidth, h: window.innerHeight });
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  const [plan, setPlan] = useState<{ keep_ranges: number[][]; duration: number; removed: number; words: { text: string; start: number; end: number }[] } | null>(null);
  // when silences/fillers are trimmed, the edit plan gives the final timeline
  const words = (plan?.words ?? rawWords) as typeof rawWords;
  const duration = plan?.duration ?? Math.max(0.5, clip.end - clip.start);

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const playRaf = useRef(0);
  const drawRaf = useRef(0);
  const lastTick = useRef(0);
  const shotRef = useRef<string | null>(null);
  const tRef = useRef(0);

  const cat = app.platforms;
  const platform = cat?.platforms[project.options.platform];
  const aspect = cat?.aspects[project.options.aspect];
  const ratio = aspect ? aspect.width / aspect.height : 9 / 16;
  // fullscreen grows the frame so captions are readable; the canvas is sized
  // from the same numbers, so the drawing stays crisp at either size
  const PHONE_W = full
    ? Math.round(
        Math.min(
          ratio >= 1 ? 1080 : FULLSCREEN_W,
          viewport.w * 0.94,
          viewport.h * 0.86 * ratio,
        ),
      )
    : ratio >= 1
      ? 440
      : MAX_PHONE_W;
  const PHONE_H = Math.round(PHONE_W / ratio);
  const safe = platform?.safe ?? { top: 0.06, bottom: 0.08, side: 0.05 };

  tRef.current = t;

  // revoke the last rendered frame on unmount so blobs do not leak
  useEffect(() => {
    shotRef.current = shot;
  }, [shot]);

  // fullscreen sizing needs to follow window resizes and Escape
  useEffect(() => {
    if (!full) return;
    const onResize = () => setViewport({ w: window.innerWidth, h: window.innerHeight });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFull(false);
    };
    onResize();
    window.addEventListener("resize", onResize);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("keydown", onKey);
    };
  }, [full]);
  useEffect(() => () => { if (shotRef.current) URL.revokeObjectURL(shotRef.current); }, []);

  useEffect(() => {
    setT(0);
    setPlaying(false);
    setShot(null);
    setShotError(null);
    setVideoReady(false);
    setVideoError(false);
    setDraft(rawWords.map((w) => w.text).join(" "));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clip.id]);

  // fetch the same keep-ranges the exporter uses
  useEffect(() => {
    let alive = true;
    setPlan(null);
    api.plan(project.id, clip.id)
      .then((p) => { if (alive) setPlan(p); })
      .catch(() => { if (alive) setPlan(null); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id, clip.id, clip.start, clip.end, project.options.remove_silences, project.options.remove_fillers]);

  // playback clock
  // The canvas paints from the video element every frame; React only needs the
  // clock for the caption and the time readout, so update that at ~30fps
  // instead of forcing a full re-render 60 times a second.
  useEffect(() => {
    if (!playing) return;
    lastTick.current = performance.now();
    const tick = (now: number) => {
      const dt = (now - lastTick.current) / 1000;
      if (now - lastTick.current < 33) {
        playRaf.current = requestAnimationFrame(tick);
        return;
      }
      lastTick.current = now;
      setT((prev) => {
        if (prev + dt >= duration) {
          setPlaying(false);
          return duration;
        }
        return prev + dt;
      });
      playRaf.current = requestAnimationFrame(tick);
    };
    playRaf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(playRaf.current);
  }, [playing, duration]);

  /** Map an edited-timeline time back to the source video (undo silence cuts). */
  const sourceTime = useCallback(
    (edited: number) => {
      const keep = plan?.keep_ranges;
      if (!keep || keep.length <= 1) return clip.start + edited;
      let remaining = edited;
      for (const [a, b] of keep) {
        const len = b - a;
        if (remaining <= len) return clip.start + a + remaining;
        remaining -= len;
      }
      return clip.end;
    },
    [plan, clip.start, clip.end],
  );

  // volume/mute apply even while paused
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    v.muted = muted;
    v.volume = volume;
  }, [muted, volume]);

  // keep the <video> near the requested time, only scrubbing when far off
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const target = sourceTime(t);
    // Snapping at 0.5s is fine while paused, but during playback it lets the
    // audio run through trimmed-out speech. Tighten it when cuts exist.
    const tol = playing && (plan?.keep_ranges?.length ?? 0) > 1 ? 0.12 : 0.5;
    if (Math.abs(v.currentTime - target) > tol) v.currentTime = target;
    if (playing && v.paused) {
      v.muted = muted;
      v.volume = volume;
      v.play().catch(() => undefined);
    }
    if (!playing && !v.paused) v.pause();
  }, [t, playing, clip.start, sourceTime, muted, volume, plan]);

  // draw the reframed frame on a canvas
  const draw = useCallback(() => {
    const v = videoRef.current;
    const canvas = canvasRef.current;
    if (!v || !canvas || !v.videoWidth) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const W = canvas.width;
    const H = canvas.height;
    const sw = v.videoWidth;
    const sh = v.videoHeight;
    ctx.clearRect(0, 0, W, H);

    const cropPlan = clip.crop_plan;
    const blur = project.options.blur_background;
    const gameplay = project.options.layout !== "standard" && !!project.options.gameplay_path;

    if (blur || gameplay) {
      // blurred cover background + fitted foreground (gameplay is simulated the same way)
      ctx.save();
      ctx.filter = "blur(14px) brightness(0.72)";
      const s = Math.max(W / sw, H / sh);
      ctx.drawImage(v, (W - sw * s) / 2, (H - sh * s) / 2, sw * s, sh * s);
      ctx.restore();
      const f = Math.min(W / sw, H / sh);
      ctx.drawImage(v, (W - sw * f) / 2, (H - sh * f) / 2, sw * f, sh * f);
    } else if (cropPlan?.crop) {
      const cw = cropPlan.crop.w;
      const ch = cropPlan.crop.h;
      const centre = cropCentre(cropPlan, sourceTime(t) - clip.start) ?? { x: sw / 2, y: sh / 2 };
      const cx = Math.max(0, Math.min(sw - cw, centre.x - cw / 2));
      const cy = Math.max(0, Math.min(sh - ch, centre.y - ch / 2));
      ctx.drawImage(v, cx, cy, cw, ch, 0, 0, W, H);
    } else {
      const targetRatio = W / H;
      const cw = Math.min(sw, sh * targetRatio);
      ctx.drawImage(v, (sw - cw) / 2, 0, cw, sh, 0, 0, W, H);
    }
  }, [clip.crop_plan, project.options.blur_background, project.options.layout, project.options.gameplay_path, t, PHONE_W, PHONE_H, sourceTime, clip.start]);

  // Redraw only when something can actually have changed. The old loop painted
  // 60 times a second even while paused, which was the biggest CPU cost in the
  // editor and made everything else feel sticky.
  useEffect(() => {
    let alive = true;
    let lastKey = "";
    let lastTime = 0;
    const loop = (now: number) => {
      if (!alive) return;
      const v = videoRef.current;
      // while paused, repaint only if the frame, style or trim changed
      const key = [
        clip.id, clip.start, clip.end,
        project.options.blur_background ? "b" : "-",
        project.options.layout,
        project.options.gameplay_path ? "g" : "-",
        canvasRef.current?.width,
        v ? Math.round(v.currentTime * 10) : 0,
        Math.round(tRef.current * 10),
      ].join("|");
      const idle = !playing;
      if (key !== lastKey || (!idle && now - lastTime > 8)) {
        lastKey = key;
        lastTime = now;
        draw();
      }
      drawRaf.current = requestAnimationFrame(loop);
    };
    drawRaf.current = requestAnimationFrame(loop);
    return () => {
      alive = false;
      cancelAnimationFrame(drawRaf.current);
    };
  }, [draw, playing, clip.id, clip.start, clip.end, project.options.blur_background, project.options.layout, project.options.gameplay_path]);

  const renderFrame = async () => {
    setBusy(true);
    setShotError(null);
    try {
      // the server renders at raw clip time; convert back from the edited timeline
      const url = await api.previewFrame(project.id, clip.id, style, Math.max(0, sourceTime(t) - clip.start));
      setShot((old) => {
        if (old) URL.revokeObjectURL(old);
        return url;
      });
    } catch (e) {
      const msg = (e as Error).message || "Preview failed";
      setShotError(msg);
      app.toast(msg, "err");
    } finally {
      setBusy(false);
    }
  };

  const commitText = () => {
    const parts = draft.trim().split(/\s+/).filter(Boolean);
    const original = words;
    if (!parts.length) {
      onClipChange({ words_override: null });
      return;
    }
    if (parts.length !== original.length) {
      app.toast(
        `Word count changed (${original.length} → ${parts.length}). Times are kept for the first ${Math.min(parts.length, original.length)} words.`,
        "info",
      );
    }
    const override = parts.map((text, i) => {
      const base = original[Math.min(i, original.length - 1)];
      return {
        text,
        start: (base?.start ?? 0) + clip.start,
        end: (base?.end ?? base?.start ?? 0) + clip.start,
      };
    });
    onClipChange({ words_override: override });
  };

  const positionPct = Math.round((style.offset ?? { top: 0.24, middle: 0.5, bottom: 0.78 }[style.position]) * 100);
  const faceInfo = useMemo(() => {
    const plan = clip.crop_plan;
    if (!plan) return "no crop plan";
    if (plan.wide) return "wide shot — try blurred background";
    return `${plan.faces_found} face samples tracked`;
  }, [clip.crop_plan]);

  return (
    <div className={"preview-col" + (full ? " preview-col-full" : "")}>
      <div className="phone-wrap">
        <div
          className="phone"
          style={{ width: PHONE_W, maxWidth: "100%", height: PHONE_H, aspectRatio: `${ratio}` }}
        >
          <video
            ref={videoRef}
            src={`/api/projects/${project.id}/source`}
            muted={muted}
            playsInline
            crossOrigin="anonymous"
            preload="auto"
            // kept in the layout (1px, invisible) rather than display:none so
            // every browser still decodes and plays its audio track
            style={{ position: "absolute", width: 1, height: 1, opacity: 0, pointerEvents: "none" }}
            onLoadedMetadata={() => {
              setVideoReady(true);
              const v = videoRef.current;
              if (v) {
                v.currentTime = clip.start;
                v.volume = volume;
                v.muted = muted;
              }
            }}
            onError={() => { setVideoReady(false); setVideoError(true); }}
          />
          {videoReady ? (
            <canvas
              ref={canvasRef}
              width={Math.round(PHONE_W * DPR)}
              height={Math.round(PHONE_H * DPR)}
              className="frame-img"
            />
          ) : (
            <div
              className="frame-img"
              style={{
                backgroundImage: clip.thumb ? `url(${clip.thumb})` : undefined,
                backgroundSize: "cover",
                backgroundPosition: "center",
                filter: "brightness(0.8)",
              }}
            />
          )}
          {videoError && (
            <div className="frame-img" style={{ display: "grid", placeItems: "center", background: "#0b0d13", padding: 18 }}>
              <span className="hint">Preview video could not load. Export still works — try “Render frame”.</span>
            </div>
          )}
          {shot && (
            <img src={shot} className="frame-img" alt="Exact rendered frame" style={{ objectFit: "cover" }} />
          )}
          {showSafe && (
            <div
              aria-hidden
              className="safe-guides"
              style={{ top: `${safe.top * 100}%`, bottom: `${safe.bottom * 100}%`, left: `${safe.side * 100}%`, right: `${safe.side * 100}%` }}
            />
          )}
          <CaptionOverlay words={words} style={style} t={t} fonts={app.fonts} width={PHONE_W} height={PHONE_H} />
          <div className="badge-live">
            {shot ? "ffmpeg frame" : "live"} · {fmtPrecise(t)} · {project.options.aspect}
          </div>
        </div>
      </div>

      <div className="card pad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button className="btn primary" style={{ minWidth: 84 }} onClick={() => setPlaying((p) => !p)}>
            {playing ? "⏸ Pause" : "▶ Play"}
          </button>
          <button className="btn small" onClick={() => { setT(0); setPlaying(false); }} title="Back to start" aria-label="Back to start">⏮</button>
          <button
            className={"btn small" + (muted ? "" : " primary")}
            onClick={() => setMuted((m) => !m)}
            aria-pressed={!muted}
            aria-label={muted ? "Unmute preview" : "Mute preview"}
            title={muted ? "Sound is off" : "Sound is on"}
          >
            {muted ? "🔇" : "🔊"}
          </button>
          <label className="range-row" style={{ width: 96 }} title="Preview volume">
            <span className="sr-only">Preview volume</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={volume}
              aria-label="Preview volume"
              onChange={(e) => {
                const v = Number(e.target.value);
                setVolume(v);
                setMuted(v === 0);
              }}
            />
          </label>
          <div className="spacer" />
          <span className="hint mono" title="Edited length after silence/filler removal">
            {fmtPrecise(t)} / {fmt(duration)}
          </span>
        </div>

        <Timeline words={words} duration={duration} t={t} onSeek={(v) => { setT(v); setPlaying(false); }} />

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn small" onClick={renderFrame} disabled={busy}>
            {busy ? <span className="spinner" /> : "📸"} Render frame
          </button>
          <button className="btn small" onClick={() => setShowText((v) => !v)}>
            {showText ? "Hide text" : "Fix text"}
          </button>
          <button className={"btn small" + (showSafe ? " primary" : "")} onClick={() => setShowSafe((v) => !v)} title="Show platform safe area">
            Safe zones
          </button>
          <button
            className={"btn small" + (full ? " primary" : "")}
            onClick={() => setFull((v) => !v)}
            aria-pressed={full}
            title={full ? "Exit big view (Esc)" : "View the clip large"}
          >
            {full ? "⤡ Exit big view" : "⤢ Big view"}
          </button>
          {shot && <button className="btn small ghost" onClick={() => setShot(null)}>Live view</button>}
          <div className="spacer" />
          <span className="hint">{faceInfo}</span>
        </div>
        {shotError && <span className="hint" style={{ color: "var(--bad)" }}>{shotError}</span>}

        {showText && (
          <div className="field">
            <label htmlFor="caption-text">
              Caption text <span className="hint">edits stay on this clip only</span>
            </label>
            <textarea
              id="caption-text"
              value={draft}
              rows={5}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commitText}
              style={{ resize: "vertical", lineHeight: 1.5, fontSize: 12.5 }}
            />
            <div style={{ display: "flex", gap: 6 }}>
              <button className="btn small" onClick={commitText}>Apply</button>
              <button
                className="btn small ghost"
                onClick={() => {
                  setDraft(words.map((w) => w.text).join(" "));
                  onClipChange({ words_override: null });
                }}
              >
                Reset
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="card pad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div className="section-title">Trim</div>
        <div className="row2">
          <div className="field">
            <label htmlFor="trim-start">Start <span className="value">{fmtPrecise(clip.start)}</span></label>
            <input
              id="trim-start"
              type="range"
              min={0}
              max={Math.max(0, project.media.duration - 1)}
              step={0.05}
              value={clip.start}
              onChange={(e) => onClipChange({ start: Math.min(Number(e.target.value), clip.end - 1) })}
            />
          </div>
          <div className="field">
            <label htmlFor="trim-end">End <span className="value">{fmtPrecise(clip.end)}</span></label>
            <input
              id="trim-end"
              type="range"
              min={1}
              max={project.media.duration}
              step={0.05}
              value={clip.end}
              onChange={(e) => onClipChange({ end: Math.max(Number(e.target.value), clip.start + 1) })}
            />
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button className="btn small" onClick={() => onClipChange({ start: Math.max(0, clip.start - 0.5) })}>◀ start</button>
          <button className="btn small" onClick={() => onClipChange({ start: Math.min(clip.end - 1, clip.start + 0.5) })}>start ▶</button>
          <button className="btn small" onClick={() => onClipChange({ end: Math.max(clip.start + 1, clip.end - 0.5) })}>◀ end</button>
          <button className="btn small" onClick={() => onClipChange({ end: Math.min(project.media.duration, clip.end + 0.5) })}>end ▶</button>
          {onSaveStyle && (
            <button className="btn small primary" onClick={() => onSaveStyle(style)}>Save style to clip</button>
          )}
        </div>
        <div className="hint">
          Source range {fmt(Math.max(0, clip.end - clip.start))} (min 1s)
          {plan && plan.removed > 0.1 ? ` · ${plan.removed.toFixed(1)}s removed → ${fmt(duration)} out` : ""}.
          Cuts snap to whole words. Captions sit {positionPct}% down the frame.
        </div>
        {plan && plan.removed > 0.1 && (
          <div className="hint" style={{ color: "var(--accent-2)" }}>
            Preview shows the trimmed timeline, matching the export.
          </div>
        )}
      </div>
    </div>
  );
}
