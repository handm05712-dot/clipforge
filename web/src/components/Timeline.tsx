import type { Word } from "../types";
import { fmtPrecise } from "../lib";

export function Timeline({
  words,
  duration,
  t,
  onSeek,
  maxHeight = 54,
}: {
  words: Word[];
  duration: number;
  t: number;
  onSeek: (t: number) => void;
  maxHeight?: number;
}) {
  const pct = (x: number) => `${Math.max(0, Math.min(100, (x / Math.max(0.01, duration)) * 100))}%`;
  const ticks = Array.from({ length: 5 }, (_, i) => (duration * i) / 4);

  return (
    <div className="timeline">
      <div
        className="track"
        style={{ height: maxHeight }}
        onPointerDown={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const seek = (clientX: number) =>
            onSeek(Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)) * duration);
          seek(e.clientX);
          const move = (ev: PointerEvent) => seek(ev.clientX);
          const up = () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
          };
          window.addEventListener("pointermove", move);
          window.addEventListener("pointerup", up);
        }}
      >
        <svg className="wave" viewBox="0 0 100 20" preserveAspectRatio="none" aria-hidden>
          {words.slice(0, 900).map((w, i) => {
            const x = (w.start / Math.max(0.01, duration)) * 100;
            const wd = Math.max(0.12, ((w.end - w.start) / Math.max(0.01, duration)) * 100);
            const h = 6 + ((w.text.length * 7 + i * 3) % 12);
            return <rect key={i} x={x} y={10 - h / 2} width={wd} height={h} fill="#4c5875" opacity={0.75} />;
          })}
        </svg>
        <div className="playhead" style={{ left: pct(t) }} />
      </div>
      <div className="ticks">
        {ticks.map((x, i) => (
          <span key={i}>{fmtPrecise(x)}</span>
        ))}
      </div>
    </div>
  );
}
