import { useEffect, useMemo, useState } from "react";
import type { CaptionStyle } from "./types";
import type { Clip, CropPlan, FontInfo, Word } from "./types";

export function fmt(t: number): string {
  const s = Math.max(0, t);
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

/** Human file size for download rows. */
export function fmtBytes(n: number): string {
  if (!n || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function fmtPrecise(t: number): string {
  const s = Math.max(0, t);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${sec.toFixed(1).padStart(4, "0")}`;
}

/** Split caption words into display lines (mirrors the backend chunker). */
export function chunkWords(words: Word[], perLine: number, maxChars = 26): Word[][] {
  const lines: Word[][] = [];
  let cur: Word[] = [];
  for (const w of words) {
    cur.push(w);
    const chars = cur.map((x) => x.text).join(" ").length;
    if (cur.length >= perLine || chars >= maxChars) {
      lines.push(cur);
      cur = [];
    }
  }
  if (cur.length) lines.push(cur);
  return lines;
}

/** Interpolate the crop window centre from keyframes at a local time. */
export function cropAt(plan: CropPlan | null | undefined, t: number): { x: number; y: number } | null {
  if (!plan || !plan.keyframes.length) return null;
  const kfs = plan.keyframes;
  if (t <= kfs[0][0]) return { x: kfs[0][1], y: kfs[0][2] };
  for (let i = 1; i < kfs.length; i++) {
    if (t <= kfs[i][0]) {
      const [t0, x0, y0] = kfs[i - 1];
      const [t1, x1, y1] = kfs[i];
      const f = t1 - t0 <= 0.0001 ? 0 : (t - t0) / (t1 - t0);
      return { x: x0 + (x1 - x0) * f, y: y0 + (y1 - y0) * f };
    }
  }
  return { x: kfs[kfs.length - 1][1], y: kfs[kfs.length - 1][2] };
}

/** Load @font-face for every installed font once, so the picker can show
 *  each family in its own typeface instead of all falling back to the
 *  system default until one is selected. */
export function ensureFontFaces(fonts: FontInfo[]) {
  if (!fonts.length) return;
  const id = "clipforge-all-font-faces";
  let el = document.getElementById(id) as HTMLStyleElement | null;
  if (!el) {
    el = document.createElement("style");
    el.id = id;
    document.head.appendChild(el);
  }
  const css = fonts
    .filter((f) => f.file)
    .map(
      (f) =>
        `@font-face{font-family:"${f.family}";src:url("/api/fonts/file/${encodeURIComponent(
          f.file as string,
        )}");font-display:swap;}`,
    )
    .join("");
  // only touch the stylesheet when the set actually changed
  if (el.textContent !== css) {
    el.textContent = css;
    fonts
      .filter((f) => f.file)
      .forEach((f) => document.fonts?.load(`16px "${f.family}"`).catch(() => undefined));
  }
}

export function useFontFace(style: CaptionStyle, fonts: FontInfo[]) {
  const [loaded, setLoaded] = useState<string | null>(null);
  const match = useMemo(
    () => fonts.find((f) => f.family.toLowerCase() === style.font.toLowerCase()),
    [fonts, style.font],
  );
  useEffect(() => {
    if (!match?.file) return;
    const id = "ff-" + match.file.replace(/[^a-z0-9]+/gi, "-");
    if (document.getElementById(id)) {
      setLoaded(style.font);
      return;
    }
    const el = document.createElement("style");
    el.id = id;
    el.textContent = `@font-face{font-family:"${style.font}";src:url("/api/fonts/file/${encodeURIComponent(
      match.file,
    )}");font-display:block;}* { --ff-${id}: 1 }`;
    document.head.appendChild(el);
    document.fonts?.load(`16px "${style.font}"`).then(() => setLoaded(style.font)).catch(() => {});
  }, [match, style.font]);
  return { fontFamily: `"${style.font}", "Archivo Black", Impact, sans-serif`, ready: loaded === style.font };
}

/** Pick the caption line that should be visible at time t (local to clip). */
export function activeLine(words: Word[], style: CaptionStyle, t: number) {
  const lines = chunkWords(words, style.words_per_line, style.max_chars);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const start = line[0].start;
    const end = line[line.length - 1].end + style.word_hold;
    if (t >= start - 0.05 && t <= end) {
      let active = -1;
      for (let j = 0; j < line.length; j++) {
        if (t >= line[j].start - 0.04 && t <= line[j].end + style.word_hold) {
          active = j;
          break;
        }
        if (t > line[j].start) active = j;
      }
      return { line, active, start, end, index: i };
    }
  }
  const next = lines.find((l) => l[0].start > t);
  return next ? { line: next, active: -1, start: next[0].start, end: next[next.length - 1].end, index: -1 } : null;
}

export function scoreClass(score: number | null): string {
  if (score == null) return "";
  if (score >= 75) return "";
  if (score >= 55) return "mid";
  return "low";
}

export function timeAgo(ts: number): string {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function useLocalWords(clip: Clip | null): Word[] {
  const words = clip?.words_override || clip?.words || [];
  if (!clip) return [];
  return words.map((w) => ({ ...w, start: w.start - clip.start, end: w.end - clip.start }));
}
