import { useState } from "react";
import { api } from "../api";
import { useApp } from "../state";
import type { Clip, SocialCopy } from "../types";

/** Keep-related platform default; the server falls back to the project's own preset. */

export function SocialCard({ projectId, clip }: { projectId: string; clip: Clip }) {
  const app = useApp();
  const [copy, setCopy] = useState<SocialCopy | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  const generate = async () => {
    setBusy(true);
    try {
      setCopy(await api.social(projectId, clip.id));
    } catch (e) {
      app.toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  };

  const copyTo = async (label: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      window.setTimeout(() => setCopied(null), 1500);
    } catch {
      app.toast("Clipboard blocked by the browser — select the text and copy it manually.", "err");
    }
  };

  return (
    <div className="card pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div className="section-title">Title, caption & hashtags</div>
        <div className="spacer" />
        <button className="btn small" onClick={generate} disabled={busy}>
          {busy ? <span className="spinner" /> : copy ? "Regenerate" : "Generate"}
        </button>
      </div>
      {!copy && (
        <span className="hint">
          Suggests a hook title, a post caption and hashtags from this clip's transcript.
        </span>
      )}
      {copy && (
        <>
          <div className="field">
            <label>Title</label>
            <div style={{ display: "flex", gap: 6 }}>
              <input readOnly value={copy.title} onFocus={(e) => e.currentTarget.select()} />
              <button className="btn small" onClick={() => copyTo("title", copy.title)} aria-label="Copy title">
                {copied === "title" ? "✓" : "Copy"}
              </button>
            </div>
          </div>
          <div className="field">
            <label>Caption</label>
            <textarea readOnly rows={3} value={copy.caption} onFocus={(e) => e.currentTarget.select()} />
          </div>
          <div className="field">
            <label>Hashtags</label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
              {copy.hashtags.map((t, i) => (
                <span key={t + i} className="chip">{t}</span>
              ))}
            </div>
          </div>
          <button
            className="btn small"
            onClick={() => copyTo("all", `${copy.title}\n\n${copy.caption}\n\n${copy.hashtags.join(" ")}`)}
          >
            {copied === "all" ? "Copied ✓" : "Copy everything"}
          </button>
          <span className="hint">Source: {copy.provider === "llm" ? "your LLM" : "offline template"}</span>
        </>
      )}
    </div>
  );
}
