import type { Clip } from "../types";
import { fmt, scoreClass } from "../lib";

export function ClipList({
  clips,
  activeId,
  onSelect,
  onDelete,
  batchMode,
  selected,
  onToggleSelected,
}: {
  clips: Clip[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  batchMode: boolean;
  selected: Set<string>;
  onToggleSelected: (id: string) => void;
}) {
  if (!clips.length) {
    return <div className="empty">No clips yet. Run analysis or add a manual clip.</div>;
  }
  return (
    <div className="clip-list">
      {clips.map((c) => (
        <div
          key={c.id}
          className={"clip-item" + (c.id === activeId ? " active" : "")}
          onClick={() => (batchMode ? onToggleSelected(c.id) : onSelect(c.id))}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => e.key === "Enter" && onSelect(c.id)}
        >
          {batchMode ? (
            <div className="thumb" style={{ display: "grid", placeItems: "center", fontSize: 18 }}>
              {selected.has(c.id) ? "☑" : "☐"}
            </div>
          ) : (
            <div
              className="thumb"
              style={c.thumb ? { backgroundImage: `url(${c.thumb})` } : undefined}
            />
          )}
          <div className="meta">
            <div className="title">{c.title || "Untitled clip"}</div>
            <div className="sub">
              <span className={"score " + scoreClass(c.score)}>
                {c.score != null ? `${Math.round(c.score)}` : "—"}
              </span>
              <span>{fmt(c.end - c.start)}</span>
              {c.export ? <span title="Exported">✓</span> : null}
              {c.wide ? <span title="Wide shot">▭</span> : null}
            </div>
          </div>
          {!batchMode && (
            <button
              className="btn ghost small"
              style={{ gridColumn: "2", justifySelf: "end", padding: "2px 6px", fontSize: 11 }}
              onClick={(e) => {
                e.stopPropagation();
                if (confirm("Delete this clip?")) onDelete(c.id);
              }}
              title="Delete clip"
            >
              ✕
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
