import { useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../state";
import type { CaptionStyle } from "../types";

const BUILTIN_META: Record<string, { label: string; desc: string }> = {
  bold_impact: { label: "Bold Impact", desc: "Chunky pop · white/yellow" },
  minimal_clean: { label: "Minimal Clean", desc: "Subtle fade · boxed" },
  neon_glow: { label: "Neon Glow", desc: "Single word · electric" },
  karaoke: { label: "Karaoke", desc: "Fill-as-you-speak" },
  typewriter: { label: "Typewriter", desc: "Terminal reveal" },
  meme_yellow: { label: "Meme Yellow", desc: "Top · headline yellow" },
};

type Props = {
  style: CaptionStyle;
  onChange: (s: CaptionStyle) => void;
  builtin: CaptionStyle[];
  currentPreset: string;
};

export function StylePanel({ style, onChange, builtin, currentPreset }: Props) {
  const app = useApp();
  const [tab, setTab] = useState<"style" | "type" | "layout">("style");
  const [gfont, setGfont] = useState("");
  const [busy, setBusy] = useState(false);
  const [presetName, setPresetName] = useState("");
  const [showSave, setShowSave] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const set = <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]) =>
    onChange({ ...style, [key]: value });

  const applyPreset = (preset: CaptionStyle) => onChange({ ...preset });

  const uploadFont = async (file: File) => {
    setBusy(true);
    try {
      const info = await api.uploadFont(file);
      await app.refreshFonts();
      set("font", info.family);
      app.toast(`Added font ${info.family}`, "ok");
    } catch (e) {
      app.toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  };

  const addGoogleFont = async () => {
    if (!gfont.trim()) return;
    setBusy(true);
    try {
      const info = await api.googleFont(gfont.trim());
      await app.refreshFonts();
      set("font", info.family);
      setGfont("");
      app.toast(`Loaded ${info.family} from Google Fonts`, "ok");
    } catch (e) {
      app.toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  };

  const savePreset = async () => {
    if (!presetName.trim()) return;
    try {
      await api.savePreset(presetName.trim(), style);
      await app.refreshPresets();
      setPresetName("");
      setShowSave(false);
      app.toast("Preset saved", "ok");
    } catch (e) {
      app.toast((e as Error).message, "err");
    }
  };

  return (
    <div className="card pad panel">
      <div>
        <div className="section-title" style={{ marginBottom: 8 }}>Caption style</div>
        <div className="preset-grid">
          {builtin.map((p) => {
            const meta = BUILTIN_META[p.preset] ?? { label: p.preset, desc: p.animation };
            return (
              <button
                key={p.preset}
                className={"preset-btn" + (currentPreset === p.preset ? " active" : "")}
                onClick={() => applyPreset(p)}
              >
                {meta.label}
                <small>{meta.desc}</small>
              </button>
            );
          })}
          {app.presets.map((p) => (
            <button
              key={p.id}
              className="preset-btn"
              onClick={() => onChange({ ...p.style })}
              title="Your saved preset"
            >
              ★ {p.name}
              <small>custom · {p.style.animation}</small>
            </button>
          ))}
        </div>
        <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
          <button className="btn small ghost" onClick={() => setShowSave((v) => !v)}>Save as preset</button>
        </div>
        {showSave && (
          <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
            <input
              value={presetName}
              onChange={(e) => setPresetName(e.target.value)}
              placeholder="My style name"
              onKeyDown={(e) => e.key === "Enter" && savePreset()}
            />
            <button className="btn small primary" onClick={savePreset}>Save</button>
          </div>
        )}
      </div>

      <div className="tabs">
        <button className={"tab" + (tab === "style" ? " active" : "")} onClick={() => setTab("style")}>Style</button>
        <button className={"tab" + (tab === "type" ? " active" : "")} onClick={() => setTab("type")}>Type</button>
        <button className={"tab" + (tab === "layout" ? " active" : "")} onClick={() => setTab("layout")}>Layout</button>
      </div>

      {tab === "style" && (
        <>
          <div className="field">
            <label>Animation</label>
            <select value={style.animation} onChange={(e) => set("animation", e.target.value as CaptionStyle["animation"])}>
              <option value="pop">Pop — active word scales up</option>
              <option value="karaoke">Karaoke — fills word by word</option>
              <option value="typewriter">Typewriter — types out</option>
              <option value="fade">Fade — clean whole line</option>
              <option value="none">Static</option>
            </select>
          </div>

          <div className="field">
            <label>Text colour</label>
            <div className="color-row">
              <input type="color" value={style.primary_color} onChange={(e) => set("primary_color", e.target.value)} />
              <input className="hex" value={style.primary_color} onChange={(e) => set("primary_color", e.target.value)} />
            </div>
          </div>
          <div className="field">
            <label>Highlight colour <span className="hint">active word</span></label>
            <div className="color-row">
              <input type="color" value={style.highlight_color} onChange={(e) => set("highlight_color", e.target.value)} />
              <input className="hex" value={style.highlight_color} onChange={(e) => set("highlight_color", e.target.value)} />
            </div>
          </div>
          <div className="row2">
            <div className="field">
              <label>Outline</label>
              <div className="color-row">
                <input type="color" value={style.outline_color} onChange={(e) => set("outline_color", e.target.value)} />
                <input className="hex" value={style.outline_color} onChange={(e) => set("outline_color", e.target.value)} />
              </div>
            </div>
            <div className="field">
              <label>Width <span className="value">{style.outline_width}</span></label>
              <input type="range" min={0} max={26} value={style.outline_width} onChange={(e) => set("outline_width", Number(e.target.value))} />
            </div>
          </div>
          <div className="row2">
            <div className="field">
              <label>Shadow <span className="value">{style.shadow}</span></label>
              <input type="range" min={0} max={20} value={style.shadow} onChange={(e) => set("shadow", Number(e.target.value))} />
            </div>
            <div className="field">
              <label>Glow <span className="value">{style.glow}</span></label>
              <input type="range" min={0} max={30} value={style.glow} onChange={(e) => set("glow", Number(e.target.value))} />
            </div>
          </div>
          <div className="field">
            <label>Glow colour</label>
            <div className="color-row">
              <input type="color" value={style.glow_color} onChange={(e) => set("glow_color", e.target.value)} />
              <input className="hex" value={style.glow_color} onChange={(e) => set("glow_color", e.target.value)} />
            </div>
          </div>
          <div className="switch-row">
            <label>Text box background</label>
            <button className={"switch" + (style.box ? " on" : "")} onClick={() => set("box", !style.box)} aria-label="toggle box" />
          </div>
          {style.box && (
            <div className="row2">
              <div className="field">
                <label>Box colour</label>
                <input type="color" value={style.box_color} onChange={(e) => set("box_color", e.target.value)} />
              </div>
              <div className="field">
                <label>Opacity <span className="value">{Math.round(style.box_opacity * 100)}%</span></label>
                <input type="range" min={0} max={1} step={0.05} value={style.box_opacity} onChange={(e) => set("box_opacity", Number(e.target.value))} />
              </div>
            </div>
          )}
          <div className="row2">
            <div className="field">
              <label>Pop scale <span className="value">{style.pop_scale}%</span></label>
              <input type="range" min={100} max={170} value={style.pop_scale} onChange={(e) => set("pop_scale", Number(e.target.value))} />
            </div>
            <div className="field">
              <label>Pop speed <span className="value">{style.pop_ms}ms</span></label>
              <input type="range" min={30} max={400} step={10} value={style.pop_ms} onChange={(e) => set("pop_ms", Number(e.target.value))} />
            </div>
          </div>
        </>
      )}

      {tab === "type" && (
        <>
          <div className="field">
            <label>Font <span className="hint">{app.fonts.length} available</span></label>
            <div className="font-list">
              {app.fonts.map((f) => (
                <button
                  key={f.family}
                  className={"font-opt" + (f.family === style.font ? " active" : "")}
                  aria-pressed={f.family === style.font}
                  onClick={() => set("font", f.family)}
                  title={f.file ? `Use ${f.family} in the render` : `${f.family} is a system font — no file to preview. Add a .ttf for an exact preview.`}
                >
                  <span className="font-name" style={{ fontFamily: `"${f.family}", sans-serif` }}>
                    {f.family}
                  </span>
                  <span className="hint" style={{ fontSize: 10.5 }}>{f.uploaded ? "mine" : f.file ? "bundled" : "system"}</span>
                </button>
              ))}
            </div>
            <div className="font-sample" style={{ fontFamily: `"${style.font}", sans-serif` }} aria-hidden="true">
              The quick brown fox 123
            </div>
            {!app.fonts.find((f) => f.family === style.font)?.file && (
              <span className="hint">
                “{style.font}” is a system font, so this sample and the frame preview use a similar fallback.
                Exports still use the exact face on the machine that renders.
              </span>
            )}
          </div>
          <div className="field">
            <label>Add Google Font</label>
            <div style={{ display: "flex", gap: 6 }}>
              <input
                value={gfont}
                onChange={(e) => setGfont(e.target.value)}
                placeholder="e.g. Bebas Neue"
                onKeyDown={(e) => e.key === "Enter" && addGoogleFont()}
              />
              <button className="btn small" onClick={addGoogleFont} disabled={busy}>
                {busy ? <span className="spinner" /> : "Add"}
              </button>
            </div>
            <span className="hint">Downloads the font so exports match the preview.</span>
          </div>
          <div className="field">
            <label>Upload font file</label>
            <input
              ref={fileRef}
              type="file"
              accept=".ttf,.otf,.ttc"
              style={{ display: "none" }}
              onChange={(e) => e.target.files?.[0] && uploadFont(e.target.files[0])}
            />
            <button className="btn small" onClick={() => fileRef.current?.click()} disabled={busy}>
              Choose .ttf / .otf
            </button>
          </div>
          <div className="field">
            <label>Font size <span className="value">{style.font_size}px</span></label>
            <input type="range" min={28} max={170} value={style.font_size} onChange={(e) => set("font_size", Number(e.target.value))} />
          </div>
          <div className="switch-row">
            <label>UPPERCASE</label>
            <button className={"switch" + (style.uppercase ? " on" : "")} onClick={() => set("uppercase", !style.uppercase)} aria-label="toggle uppercase" />
          </div>
          <div className="field">
            <label>Letter spacing <span className="value">{style.letter_spacing}</span></label>
            <input type="range" min={-3} max={12} value={style.letter_spacing} onChange={(e) => set("letter_spacing", Number(e.target.value))} />
          </div>
          <div className="switch-row">
            <label>Auto emoji</label>
            <button className={"switch" + (style.emoji ? " on" : "")} onClick={() => set("emoji", !style.emoji)} aria-label="toggle emoji" />
          </div>
          {style.emoji && (
            <div className="field">
              <label>Emoji every <span className="value">{style.emoji_every} words</span></label>
              <input type="range" min={4} max={40} value={style.emoji_every} onChange={(e) => set("emoji_every", Number(e.target.value))} />
            </div>
          )}
        </>
      )}

      {tab === "layout" && (
        <>
          <div className="field">
            <label>Vertical position</label>
            <select value={style.position} onChange={(e) => set("position", e.target.value as CaptionStyle["position"])}>
              <option value="top">Top</option>
              <option value="middle">Middle</option>
              <option value="bottom">Bottom</option>
            </select>
          </div>
          <div className="field">
            <label>Fine offset <span className="value">{Math.round((style.offset ?? 0.78) * 100)}%</span></label>
            <input
              type="range"
              min={0.1}
              max={0.92}
              step={0.005}
              value={style.offset ?? 0.78}
              onChange={(e) => set("offset", Number(e.target.value))}
            />
          </div>
          <div className="field">
            <label>Words per line</label>
            <div className="tabs">
              {[1, 2, 3].map((n) => (
                <button
                  key={n}
                  className={"tab" + (style.words_per_line === n ? " active" : "")}
                  onClick={() => set("words_per_line", n)}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>
          <div className="field">
            <label>Max chars per line <span className="value">{style.max_chars}</span></label>
            <input type="range" min={8} max={48} value={style.max_chars} onChange={(e) => set("max_chars", Number(e.target.value))} />
          </div>
          <div className="field">
            <label>Word hold <span className="value">{style.word_hold.toFixed(2)}s</span></label>
            <input type="range" min={0} max={0.5} step={0.02} value={style.word_hold} onChange={(e) => set("word_hold", Number(e.target.value))} />
          </div>
          <div className="field">
            <label>Side margin <span className="value">{style.margin_side}</span></label>
            <input type="range" min={20} max={240} value={style.margin_side} onChange={(e) => set("margin_side", Number(e.target.value))} />
          </div>
        </>
      )}
    </div>
  );
}
