import { memo } from "react";
import type { CaptionStyle, Word } from "../types";
import { activeLine, useFontFace } from "../lib";
import type { FontInfo } from "../types";

const Y: Record<string, number> = { top: 0.2, middle: 0.5, bottom: 0.78 };

export function captionStyleCss(style: CaptionStyle, fontFamily: string): React.CSSProperties {
  const glow = style.glow
    ? `0 0 ${style.glow}px ${style.glow_color}, 0 0 ${style.glow * 2}px ${style.glow_color}66`
    : "";
  const shadows = [
    glow,
    style.shadow ? `${style.shadow * 0.8}px ${style.shadow * 0.8}px 0 ${style.shadow_color}` : "",
  ].filter(Boolean).join(", ");
  const textShadow = style.outline_width
    ? outlineShadow(style.outline_width * 0.9, style.outline_color) + (shadows ? ", " + shadows : "")
    : shadows || "none";
  return {
    fontFamily,
    fontWeight: 800,
    letterSpacing: `${style.letter_spacing}px`,
    textShadow,
    WebkitTextStroke: style.box ? undefined : `${Math.min(style.outline_width * 0.22, 4)}px transparent`,
    filter: style.blur_edges ? "blur(0.3px)" : undefined,
  };
}

function outlineShadow(w: number, color: string): string {
  const steps: string[] = [];
  const n = Math.max(4, Math.round(w));
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    steps.push(`${(Math.cos(a) * w).toFixed(2)}px ${(Math.sin(a) * w).toFixed(2)}px 0 ${color}`);
  }
  return steps.join(", ");
}

/** Memoised: during playback the parent ticks 60 times a second, but the
 *  caption only changes when the active word changes. */
export const CaptionOverlay = memo(function CaptionOverlayInner({
  words,
  style,
  t,
  fonts,
  width = 330,
}: {
  words: Word[];
  style: CaptionStyle;
  t: number;
  fonts: FontInfo[];
  width?: number;
  height?: number;
}) {
  const { fontFamily } = useFontFace(style, fonts);
  const found = activeLine(words, style, t);
  if (!found) return null;
  const { line, active } = found;
  const pos = style.offset ?? Y[style.position] ?? 0.78;

  return (
    <div
      className="overlay-caption"
      style={{
        top: `${pos * 100}%`,
        left: `${(style.margin_side / 1080) * 100}%`,
        right: `${(style.margin_side / 1080) * 100}%`,
        maxHeight: "56%",
        overflow: "hidden",
        ...captionStyleCss(style, fontFamily),
      }}
    >
      {style.box && (
        <div
          style={{
            position: "absolute",
            inset: `${-style.box_padding * 0.4}px ${-style.box_padding * 0.8}px`,
            background: hexA(style.box_color, style.box_opacity),
            borderRadius: 10,
            zIndex: -1,
          }}
        />
      )}
      <div
        style={{
          // never smaller than 12px in the editor: a 58px caption on a 330px
          // frame worked out at ~18px and was hard to read
          fontSize: Math.max(12, style.font_size * (width / 1080)),
          lineHeight: 1.1,
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "center",
          gap: "0 0.28em",
          overflowWrap: "anywhere",
          wordBreak: "break-word",
          textWrap: "balance",
        }}
      >
        {line.map((w, i) => {
          const isActive = i === active;
          const text = style.uppercase ? w.text.toUpperCase() : w.text;
          const karaoke = style.animation === "karaoke";
          const sung = karaoke && (t > w.start || i < active);
          const visible = style.animation === "typewriter" ? i <= active : true;
          return (
            <span
              key={i}
              style={{
                display: visible ? "inline-block" : "none",
                color: isActive || sung ? style.highlight_color : style.primary_color,
                transform: isActive && (style.animation === "pop" || style.animation === "karaoke")
                  ? `scale(${style.pop_scale / 100}) rotate(-1.5deg)`
                  : "scale(1)",
                transition: `transform ${style.pop_ms}ms cubic-bezier(.2,1.6,.4,1), color 90ms`,
                willChange: "transform",
              }}
            >
              {text}
              {w.emoji ? " " + w.emoji : ""}
              {style.animation === "typewriter" && i === active && i < line.length - 1 ? "▌" : ""}
            </span>
          );
        })}
      </div>
    </div>
  );
});

export function hexA(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const a = Math.round(Math.max(0, Math.min(1, alpha)) * 255).toString(16).padStart(2, "0");
  return `#${h.slice(0, 6)}${a}`;
}

export function swatch(hex: string) {
  return <span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 3, background: hex, border: "1px solid #333" }} />;
}
