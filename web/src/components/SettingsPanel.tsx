import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../state";
import type { Job, Project, SfxItem } from "../types";

type Props = {
  project: Project;
  onProject: (p: Project) => void;
  onReanalyze: () => void;
  onApplyBrand: () => void;
};

type Tab = "output" | "audio" | "brand" | "detect";

export function SettingsPanel({ project, onProject, onReanalyze, onApplyBrand }: Props) {
  const app = useApp();
  const musicRef = useRef<HTMLInputElement>(null);
  const wmRef = useRef<HTMLInputElement>(null);
  const gameRef = useRef<HTMLInputElement>(null);
  const sfxRef = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<Tab>("output");
  const [uploading, setUploading] = useState<string | null>(null);
  const opts = project.options;
  const cat = app.platforms;
  const platform = cat?.platforms[opts.platform];
  const aspect = cat?.aspects[opts.aspect];
  const busy = app.activeJobs.some((j: Job) => j.project_id === project.id);

  // Slider drags would otherwise fire a request (and an undo step) per pixel.
  // Show the change instantly, then save once the user stops moving.
  const pending = useRef<Partial<typeof opts>>({});
  const saveTimer = useRef<number>(0);
  const flush = async () => {
    const body = pending.current;
    pending.current = {};
    if (!Object.keys(body).length) return;
    try {
      await api.patchProject(project.id, { options: body });
    } catch (e) {
      app.toast((e as Error).message, "err");
    }
  };
  useEffect(() => () => { void flush(); window.clearTimeout(saveTimer.current); }, []);

  const patch = (partial: Partial<typeof opts>) => {
    const next = { ...opts, ...pending.current, ...partial };
    onProject({ ...project, options: next });
    pending.current = { ...pending.current, ...partial };
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => { void flush(); }, 350);
  };

  /** Changing platform also suggests its length window and aspect. */
  const pickPlatform = (name: string) => {
    const p = cat?.platforms[name];
    if (!p) return;
    void patch({
      platform: name,
      aspect: p.aspect,
      clip_min: p.min_len,
      clip_max: p.max_len,
    });
  };

  const upload = async (kind: "music" | "watermark" | "gameplay" | "sfx", file: File, sfxAt = 0) => {
    setUploading(kind);
    try {
      const info = await api.uploadAsset(kind as "music" | "watermark", file);
      if (kind === "music") await patch({ music_path: info.path });
      else if (kind === "watermark") await patch({ watermark_path: info.path });
      else if (kind === "gameplay") await patch({ gameplay_path: info.path });
      else {
        const item: SfxItem = {
          id: `fx-${Date.now()}`,
          path: info.path,
          name: file.name,
          at: sfxAt,
          volume: 0.6,
          duration: 3,
        };
        await patch({ sfx: [...(opts.sfx || []), item] });
      }
      app.toast(`${kind === "gameplay" ? "Gameplay clip" : kind === "sfx" ? "Sound effect" : kind} added`, "ok");
    } catch (e) {
      app.toast((e as Error).message, "err");
    } finally {
      setUploading(null);
    }
  };

  const setSfx = (id: string, partial: Partial<SfxItem>) =>
    patch({ sfx: (opts.sfx || []).map((s) => (s.id === id ? { ...s, ...partial } : s)) });

  const removeSfx = (id: string) => patch({ sfx: (opts.sfx || []).filter((s) => s.id !== id) });

  const clipDuration = useMemo(() => {
    const c = project.clips[0];
    return c ? Math.round(c.end - c.start) : 30;
  }, [project.clips]);

  return (
    <div className="card pad panel">
      <div className="tabs" role="tablist" aria-label="Video settings sections">
        {([
          ["output", "Output"],
          ["audio", "Audio & layout"],
          ["brand", "Brand"],
          ["detect", "Clips"],
        ] as [Tab, string][]).map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            className={"tab" + (tab === id ? " active" : "")}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "output" && (
        <>
          <div className="field">
            <label htmlFor="platform-select">Platform preset</label>
            <select
              id="platform-select"
              value={opts.platform}
              onChange={(e) => pickPlatform(e.target.value)}
            >
              {Object.entries(cat?.platforms ?? {}).map(([id, p]) => (
                <option key={id} value={id}>{p.label}</option>
              ))}
            </select>
            {platform && <span className="hint">{platform.note}</span>}
          </div>

          <div className="field">
            <label htmlFor="aspect-select">Aspect ratio</label>
            <select
              id="aspect-select"
              value={opts.aspect}
              onChange={(e) => patch({ aspect: e.target.value })}
            >
              {Object.entries(cat?.aspects ?? {}).map(([id, a]) => (
                <option key={id} value={id}>{a.label} · {a.width}×{a.height}</option>
              ))}
            </select>
            {aspect && (
              <span className="hint">
                Export size {aspect.width}×{aspect.height}. Captions and the logo stay inside the
                platform safe area.
              </span>
            )}
          </div>

          <div className="field">
            <label>Quality</label>
            <div className="tabs">
              {Object.entries(cat?.quality ?? {}).map(([id, q]) => (
                <button
                  key={id}
                  className={"tab" + (opts.quality === id ? " active" : "")}
                  onClick={() => patch({ quality: id })}
                  title={`CRF ${q.crf} · ${q.preset} preset`}
                >
                  {q.label}
                </button>
              ))}
            </div>
          </div>

          <div className="field">
            <label>Reframing</label>
            <select
              value={opts.reframe}
              onChange={(e) => patch({ reframe: e.target.value as typeof opts.reframe })}
            >
              <option value="face">Face tracking (speaker centred)</option>
              <option value="center">Centre crop</option>
            </select>
          </div>

          <div className="switch-row">
            <label htmlFor="blur-toggle">Blurred background for wide shots</label>
            <button
              id="blur-toggle"
              className={"switch" + (opts.blur_background ? " on" : "")}
              onClick={() => patch({ blur_background: !opts.blur_background })}
              role="switch"
              aria-checked={opts.blur_background}
            />
          </div>
        </>
      )}

      {tab === "audio" && (
        <>
          <div className="switch-row">
            <label htmlFor="silence-toggle">Remove silences</label>
            <button
              id="silence-toggle"
              className={"switch" + (opts.remove_silences ? " on" : "")}
              onClick={() => patch({ remove_silences: !opts.remove_silences })}
              role="switch"
              aria-checked={opts.remove_silences}
            />
          </div>
          <div className="switch-row">
            <label htmlFor="filler-toggle">Remove filler words (um, uh)</label>
            <button
              id="filler-toggle"
              className={"switch" + (opts.remove_fillers ? " on" : "")}
              onClick={() => patch({ remove_fillers: !opts.remove_fillers })}
              role="switch"
              aria-checked={opts.remove_fillers}
            />
          </div>
          <div className="switch-row">
            <label htmlFor="voice-toggle">Voice clarity boost</label>
            <button
              id="voice-toggle"
              className={"switch" + (opts.voice_boost ? " on" : "")}
              onClick={() => patch({ voice_boost: !opts.voice_boost })}
              role="switch"
              aria-checked={opts.voice_boost}
            />
          </div>

          <div className="section-title" style={{ marginTop: 4 }}>Gameplay layout</div>
          <div className="field">
            <label htmlFor="layout-select">Layout</label>
            <select
              id="layout-select"
              value={opts.layout || "standard"}
              onChange={(e) => patch({ layout: e.target.value as typeof opts.layout })}
            >
              <option value="standard">Standard — talking head</option>
              <option value="gameplay_top">Gameplay on top, facecam below</option>
              <option value="gameplay_full">Full-screen gameplay + facecam corner</option>
            </select>
            <span className="hint">
              The uploaded gameplay clip is cut to the same time range as the talking-head source.
            </span>
          </div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <span className="hint" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {opts.gameplay_path ? opts.gameplay_path.split("/").pop() : "No gameplay clip"}
            </span>
            <input
              ref={gameRef}
              type="file"
              accept="video/mp4,video/webm,video/quicktime"
              style={{ display: "none" }}
              onChange={(e) => e.target.files?.[0] && upload("gameplay", e.target.files[0])}
            />
            <button
              className="btn small"
              onClick={() => gameRef.current?.click()}
              disabled={uploading === "gameplay"}
            >
              {uploading === "gameplay" ? <span className="spinner" /> : "Upload"}
            </button>
            {opts.gameplay_path && (
              <button
                className="btn small ghost danger"
                onClick={() => patch({ gameplay_path: null })}
                aria-label="Remove gameplay clip"
              >
                ×
              </button>
            )}
          </div>

          <div className="section-title" style={{ marginTop: 4 }}>Background music</div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <span className="hint" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {opts.music_path ? opts.music_path.split("/").pop() : "No music"}
            </span>
            <input
              ref={musicRef}
              type="file"
              accept=".mp3,.wav,.m4a,.aac,.ogg,.flac"
              style={{ display: "none" }}
              onChange={(e) => e.target.files?.[0] && upload("music", e.target.files[0])}
            />
            <button
              className="btn small"
              onClick={() => musicRef.current?.click()}
              disabled={uploading === "music"}
            >
              {uploading === "music" ? <span className="spinner" /> : "Upload"}
            </button>
            {opts.music_path && (
              <button
                className="btn small ghost danger"
                onClick={() => patch({ music_path: null })}
                aria-label="Remove music"
              >
                ×
              </button>
            )}
          </div>
          {opts.music_path && (
            <>
              <div className="field">
                <label htmlFor="music-vol">Music volume <span className="value">{Math.round(opts.music_volume * 100)}%</span></label>
                <input
                  id="music-vol"
                  type="range"
                  min={0}
                  max={1}
                  step={0.02}
                  value={opts.music_volume}
                  onChange={(e) => patch({ music_volume: Number(e.target.value) })}
                />
              </div>
              <div className="switch-row">
                <label htmlFor="duck-toggle">Auto-duck under speech</label>
                <button
                  id="duck-toggle"
                  className={"switch" + (opts.auto_duck ? " on" : "")}
                  onClick={() => patch({ auto_duck: !opts.auto_duck })}
                  role="switch"
                  aria-checked={opts.auto_duck}
                />
              </div>
            </>
          )}

          <div className="section-title" style={{ marginTop: 4 }}>Sound effects</div>
          <span className="hint">
            Added at a time inside the edited clip (0–{clipDuration}s). Useful for whooshes and pops.
          </span>
          <input
            ref={sfxRef}
            type="file"
            accept=".mp3,.wav,.m4a,.ogg"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) upload("sfx", f);
              e.target.value = "";
            }}
          />
          <button
            className="btn small"
            onClick={() => sfxRef.current?.click()}
            disabled={uploading === "sfx"}
          >
            {uploading === "sfx" ? <span className="spinner" /> : "+ Add sound effect"}
          </button>
          {(opts.sfx || []).map((s) => (
            <div key={s.id} className="card" style={{ padding: 8, display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <span className="hint" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {s.name}
                </span>
                <button className="btn small ghost danger" onClick={() => removeSfx(s.id)} aria-label={`Remove ${s.name}`}>×</button>
              </div>
              <div className="row2">
                <div className="field">
                  <label>At <span className="value">{s.at.toFixed(1)}s</span></label>
                  <input
                    type="range"
                    min={0}
                    max={Math.max(1, clipDuration)}
                    step={0.1}
                    value={s.at}
                    onChange={(e) => setSfx(s.id, { at: Number(e.target.value) })}
                  />
                </div>
                <div className="field">
                  <label>Volume <span className="value">{Math.round(s.volume * 100)}%</span></label>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={s.volume}
                    onChange={(e) => setSfx(s.id, { volume: Number(e.target.value) })}
                  />
                </div>
              </div>
            </div>
          ))}
        </>
      )}

      {tab === "brand" && (
        <>
          <span className="hint">
            Save a logo, colours and font once, then apply them to this and every future project.
          </span>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <span className="hint" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {app.brand?.name ? app.brand.name : "No brand saved"}
            </span>
            <button className="btn small primary" onClick={onApplyBrand}>Apply brand kit</button>
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            {(app.brand?.colors || []).map((c, i) => (
              <span key={c + i} title={c} style={{ width: 22, height: 22, borderRadius: 5, background: c, border: "1px solid var(--line-2)" }} />
            ))}
          </div>

          <div className="section-title" style={{ marginTop: 4 }}>Watermark</div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <span className="hint" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {opts.watermark_path ? opts.watermark_path.split("/").pop() : "No logo"}
            </span>
            <input
              ref={wmRef}
              type="file"
              accept=".png,.jpg,.jpeg,.webp"
              style={{ display: "none" }}
              onChange={(e) => e.target.files?.[0] && upload("watermark", e.target.files[0])}
            />
            <button className="btn small" onClick={() => wmRef.current?.click()} disabled={uploading === "watermark"}>
              {uploading === "watermark" ? <span className="spinner" /> : "Upload"}
            </button>
            {opts.watermark_path && (
              <button className="btn small ghost danger" onClick={() => patch({ watermark_path: null })} aria-label="Remove watermark">×</button>
            )}
          </div>
          {opts.watermark_path && (
            <>
              <div className="row2">
                <div className="field">
                  <label htmlFor="wm-pos">Position</label>
                  <select id="wm-pos" value={opts.watermark_position} onChange={(e) => patch({ watermark_position: e.target.value })}>
                    <option value="top-left">Top left</option>
                    <option value="top-right">Top right</option>
                    <option value="bottom-left">Bottom left</option>
                    <option value="bottom-right">Bottom right</option>
                    <option value="center">Center</option>
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="wm-width">Width <span className="value">{opts.watermark_width}px</span></label>
                  <input id="wm-width" type="range" min={60} max={480} step={10} value={opts.watermark_width} onChange={(e) => patch({ watermark_width: Number(e.target.value) })} />
                </div>
              </div>
              <div className="field">
                <label htmlFor="wm-opacity">Opacity <span className="value">{Math.round(opts.watermark_opacity * 100)}%</span></label>
                <input id="wm-opacity" type="range" min={0.1} max={1} step={0.05} value={opts.watermark_opacity} onChange={(e) => patch({ watermark_opacity: Number(e.target.value) })} />
              </div>
            </>
          )}
        </>
      )}

      {tab === "detect" && (
        <>
          <div className="row2">
            <div className="field">
              <label htmlFor="clip-count">Clips <span className="value">{opts.clip_count}</span></label>
              <input id="clip-count" type="range" min={1} max={10} value={opts.clip_count} onChange={(e) => patch({ clip_count: Number(e.target.value) })} />
            </div>
            <div className="field">
              <label htmlFor="provider-select">Provider</label>
              <select id="provider-select" value={opts.provider} onChange={(e) => patch({ provider: e.target.value as typeof opts.provider })}>
                <option value="auto">Auto (LLM → fallback)</option>
                <option value="llm">LLM only</option>
                <option value="heuristic">Offline scoring</option>
              </select>
            </div>
          </div>
          <div className="row2">
            <div className="field">
              <label htmlFor="clip-min">Min length <span className="value">{opts.clip_min}s</span></label>
              <input id="clip-min" type="range" min={5} max={45} value={opts.clip_min} onChange={(e) => patch({ clip_min: Number(e.target.value) })} />
            </div>
            <div className="field">
              <label htmlFor="clip-max">Max length <span className="value">{opts.clip_max}s</span></label>
              <input id="clip-max" type="range" min={30} max={90} value={opts.clip_max} onChange={(e) => patch({ clip_max: Number(e.target.value) })} />
            </div>
          </div>
          <button className="btn" onClick={onReanalyze} disabled={busy}>
            {busy ? <><span className="spinner" /> Working…</> : "Re-detect clips"}
          </button>
          <span className="hint">Reuses the saved transcript, so it is fast.</span>
        </>
      )}
    </div>
  );
}
