import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../state";
import { fmt, fmtBytes } from "../lib";
import type { CaptionStyle, Clip, HistoryInfo, Job, Project } from "../types";
import { ClipList } from "./ClipList";
import { PreviewColumn } from "./PreviewColumn";
import { SettingsPanel } from "./SettingsPanel";
import { SocialCard } from "./SocialCard";
import { StylePanel } from "./StylePanel";

export function Editor({ projectId }: { projectId: string }) {
  const app = useApp();
  const [project, setProject] = useState<Project | null>(null);
  const [builtin, setBuiltin] = useState<CaptionStyle[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [batch, setBatch] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [tab, setTab] = useState<"captions" | "video">("captions");
  const [style, setStyle] = useState<CaptionStyle | null>(null);
  const [presetName, setPresetName] = useState("bold_impact");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [hist, setHist] = useState<HistoryInfo>({ can_undo: false, can_redo: false, undo_label: null });
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved">("idle");
  const saveTimer = useRef<number>(0);
  const [files, setFiles] = useState<{ name: string; url: string; size: number; created: number; title?: string }[]>([]);

  const load = useCallback(async () => {
    try {
      const [p, pres] = await Promise.all([api.project(projectId), api.presets()]);
      setProject(p);
      setBuiltin(pres.builtin);
      setLoadError(null);
      setActiveId((cur) => (cur && p.clips.some((c) => c.id === cur) ? cur : p.clips[0]?.id ?? null));
      api.history(projectId).then(setHist).catch(() => undefined);
      api.exports(projectId).then((r) => setFiles(r.files)).catch(() => undefined);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Could not load this project");
    }
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  // settings edits happen inside SettingsPanel, so re-read history when options change
  useEffect(() => {
    if (!project) return;
    const t = window.setTimeout(() => {
      api.history(projectId).then(setHist).catch(() => undefined);
    }, 350);
    return () => window.clearTimeout(t);
  }, [project?.options, projectId, project]);

  const projectJob = app.jobs.find((j: Job) => j.project_id === projectId);
  const analyzeJob: Job | null =
    projectJob && (projectJob.kind === "analyze" || projectJob.kind === "download") ? projectJob : null;
  const analyzing = analyzeJob != null && (analyzeJob.status === "running" || analyzeJob.status === "queued");

  useEffect(() => {
    if (analyzeJob?.status === "done") load();
  }, [analyzeJob?.status, load]);

  const clip: Clip | null = useMemo(
    () => project?.clips.find((c) => c.id === activeId) ?? project?.clips[0] ?? null,
    [project, activeId],
  );

  useEffect(() => {
    if (!clip) {
      setStyle(null);
      return;
    }
    const next = clip.style ?? project?.style ?? builtin.find((b) => b.preset === (clip.style_preset ?? "bold_impact")) ?? builtin[0] ?? null;
    if (next) {
      setStyle(next);
      setPresetName(clip.style_preset ?? next.preset ?? "custom");
    }
  }, [clip, project?.style, builtin]);

  const flashSaved = useCallback(() => {
    setSaveState("saved");
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => setSaveState("idle"), 1600);
  }, []);

  useEffect(() => () => window.clearTimeout(saveTimer.current), []);

  const saveStyle = useCallback(
    async (next: CaptionStyle, preset?: string) => {
      setStyle(next);
      if (preset) setPresetName(preset);
      if (!clip) return;
      setSaveState("saving");
      try {
        const updated = await api.patchClip(projectId, clip.id, {
          style: next,
          ...(preset ? { style_preset: preset } : {}),
          clear_export: true,
        });
        setProject((p) =>
          p ? { ...p, clips: p.clips.map((c) => (c.id === updated.id ? { ...c, ...updated } : c)) } : p,
        );
        flashSaved();
        api.history(projectId).then(setHist).catch(() => undefined);
      } catch (e) {
        app.toast((e as Error).message, "err");
      }
    },
    [clip, projectId, app, flashSaved],
  );

  /** Debounced style saving so dragging a colour picker does not spam the API. */
  const styleTimer = useRef<number>(0);
  const pendingStyle = useRef<CaptionStyle | null>(null);
  const queueStyle = useCallback(
    (next: CaptionStyle) => {
      setStyle(next);
      pendingStyle.current = next;
      setSaveState("saving");
      window.clearTimeout(styleTimer.current);
      styleTimer.current = window.setTimeout(() => {
        if (pendingStyle.current) void saveStyle(pendingStyle.current);
      }, 420);
    },
    [saveStyle],
  );
  useEffect(() => () => window.clearTimeout(styleTimer.current), []);

  const patchClip = useCallback(
    async (patch: Partial<Clip>) => {
      if (!clip) return;
      const optimistic = { ...clip, ...patch };
      if ((patch.start != null || patch.end != null) && project?.transcript) {
        const start = optimistic.start;
        const end = optimistic.end;
        optimistic.words = project.transcript.words.filter((w) => w.end > start && w.start < end);
        optimistic.words_override = undefined;
      }
      setProject((p) => (p ? { ...p, clips: p.clips.map((c) => (c.id === clip.id ? optimistic : c)) } : p));
      setSaveState("saving");
      try {
        const updated = await api.patchClip(projectId, clip.id, { ...patch, clear_export: true });
        setProject((p) =>
          p ? { ...p, clips: p.clips.map((c) => (c.id === clip.id ? { ...c, ...updated } : c)) } : p,
        );
        flashSaved();
        api.history(projectId).then(setHist).catch(() => undefined);
      } catch (e) {
        app.toast((e as Error).message, "err");
        load();
      }
    },
    [clip, project, projectId, app, load, flashSaved],
  );

  const doHistory = useCallback(
    async (action: "undo" | "redo") => {
      try {
        const res = action === "undo" ? await api.undo(projectId) : await api.redo(projectId);
        setProject(res.project);
        setActiveId((cur) =>
          cur && res.project.clips.some((c) => c.id === cur) ? cur : res.project.clips[0]?.id ?? null,
        );
        setHist({ can_undo: res.can_undo, can_redo: res.can_redo, undo_label: res.undo_label });
        app.toast(action === "undo" ? "Undone" : "Redone", "info");
      } catch (e) {
        app.toast((e as Error).message, "err");
      }
    },
    [projectId, app],
  );

  // keyboard undo/redo, ignored while typing
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName ?? "";
      if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) return;
      const mod = e.metaKey || e.ctrlKey;
      if (!mod) return;
      if (e.key.toLowerCase() === "z" && !e.shiftKey) {
        e.preventDefault();
        void doHistory("undo");
      } else if ((e.key.toLowerCase() === "z" && e.shiftKey) || e.key.toLowerCase() === "y") {
        e.preventDefault();
        void doHistory("redo");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [doHistory]);

  const exportOne = async () => {
    if (!clip) return;
    try {
      const { job_id } = await api.exportClip(projectId, clip.id, style ?? undefined);
      app.toast("Export started — this can take a minute or two.", "info");
      app.watchJob(job_id, () => { load(); });

    } catch (e) {
      app.toast((e as Error).message, "err");
    }
  };

  const exportBatch = async () => {
    const ids = batch && selected.size ? Array.from(selected) : (project?.clips ?? []).map((c) => c.id);
    if (!ids.length) {
      app.toast("Select at least one clip to export.", "info");
      return;
    }
    try {
      const { job_id } = await api.exportBatch(projectId, ids);
      app.toast(`Batch export started for ${ids.length} clips.`, "info");
      app.watchJob(job_id, () => load());
    } catch (e) {
      app.toast((e as Error).message, "err");
    }
  };

  const exportJob = app.jobs.find(
    (j) => (j.kind === "export" || j.kind === "batch") && j.project_id === projectId,
  );
  const exporting = exportJob != null && (exportJob.status === "running" || exportJob.status === "queued");

  const deleteClip = async (id: string) => {
    try {
      await api.deleteClip(projectId, id);
      setActiveId(null);
      await load();
      app.toast("Clip deleted — undo with Ctrl+Z", "info");
    } catch (e) {
      app.toast((e as Error).message, "err");
    }
  };

  const applyBrand = async () => {
    try {
      const p = await api.applyBrand(projectId);
      setProject(p);
      app.toast("Brand kit applied to this project", "ok");
    } catch (e) {
      app.toast((e as Error).message, "err");
    }
  };

  if (loadError) {
    return (
      <div className="empty">
        <p style={{ color: "var(--bad)" }}>{loadError}</p>
        <div style={{ display: "flex", gap: 8, justifyContent: "center", marginTop: 12 }}>
          <button className="btn" onClick={load}>Retry</button>
          <button className="btn ghost" onClick={app.goHome}>Back to projects</button>
        </div>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="empty">
        <span className="spinner" /> Loading project…
      </div>
    );
  }

  return (
    <div className="editor-page" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="editor-head">
        <button className="btn small ghost" onClick={app.goHome}>← Projects</button>
        <div style={{ minWidth: 0, flex: 1 }}>
          <h2 style={{ fontSize: 17, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {project.title}
          </h2>
          <div className="hint">
            {fmt(project.media.duration)} · {project.media.width}×{project.media.height} ·{" "}
            {project.transcript?.language ?? "—"} · {project.clips.length} clips
            {project.analysis_provider ? ` · ${project.analysis_provider}` : ""}
            {" · "}
            <span aria-live="polite">
              {saveState === "saving" ? "Saving…" : saveState === "saved" ? "Saved ✓" : "Auto-saved"}
            </span>
          </div>
        </div>
        <div className="editor-actions">
          <button
            className="btn small"
            onClick={() => doHistory("undo")}
            disabled={!hist.can_undo}
            title={hist.undo_label ? `Undo ${hist.undo_label}` : "Nothing to undo"}
            aria-label="Undo"
          >
            ↶ Undo
          </button>
          <button
            className="btn small"
            onClick={() => doHistory("redo")}
            disabled={!hist.can_redo}
            aria-label="Redo"
          >
            ↷ Redo
          </button>
          <button className={"btn small" + (batch ? " primary" : "")} onClick={() => setBatch((b) => !b)}>
            {batch ? `Batch (${selected.size})` : "Batch select"}
          </button>
          <button className="btn primary" onClick={exportOne} disabled={!clip || exporting}>
            {exporting ? <><span className="spinner" /> Exporting…</> : "⬇ Export clip"}
          </button>
          <button className="btn" onClick={exportBatch} disabled={!project.clips.length || exporting}>
            Export all
          </button>
        </div>
      </div>

      {(analyzing || exportJob) && (
        <div className="card pad" style={{ display: "flex", flexDirection: "column", gap: 8 }} aria-live="polite">
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
            <span>
              {analyzing ? `Analysing · ${analyzeJob?.stage}` : `Exporting · ${exportJob?.stage}`}
              {analyzeJob?.message ? <span className="hint"> — {analyzeJob.message}</span> : null}
            </span>
            <span className="hint">
              {Math.round((analyzing ? analyzeJob?.progress ?? 0 : exportJob?.progress ?? 0) * 100)}%
            </span>
          </div>
          <div className="progress" role="progressbar" aria-valuenow={Math.round((analyzing ? analyzeJob?.progress ?? 0 : exportJob?.progress ?? 0) * 100)} aria-valuemin={0} aria-valuemax={100}>
            <div style={{ width: `${Math.round((analyzing ? analyzeJob?.progress ?? 0 : exportJob?.progress ?? 0) * 100)}%` }} />
          </div>
          {exportJob?.status === "error" && (
            <span className="hint" style={{ color: "var(--bad)" }}>{exportJob.error || "Export failed"}</span>
          )}
        </div>
      )}

      {project.status === "transcribed" && !project.clips.length && !analyzing && (
        <div className="card pad hint">Transcript ready but no clips yet. Use “Re-detect clips”.</div>
      )}

      <div className="editor">
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div className="section-title">Clips</div>
            <div className="spacer" />
            <button
              className="btn small ghost"
              title="Add a clip from the current transcript"
              onClick={async () => {
                try {
                  const start = clip ? clip.end : 0;
                  const end = Math.min(project.media.duration, start + 30);
                  if (end - start < 1) {
                    app.toast("No room for another clip at the end of the video.", "info");
                    return;
                  }
                  const c = await api.addClip(projectId, start, end);
                  setActiveId(c.id);
                  await load();
                } catch (e) {
                  app.toast((e as Error).message, "err");
                }
              }}
            >
              + Add
            </button>
          </div>
          <ClipList
            clips={project.clips}
            activeId={clip?.id ?? null}
            onSelect={setActiveId}
            onDelete={deleteClip}
            batchMode={batch}
            selected={selected}
            onToggleSelected={(id) =>
              setSelected((s) => {
                const next = new Set(s);
                if (next.has(id)) next.delete(id);
                else next.add(id);
                return next;
              })
            }
          />
          {files.length ? (
            <div className="card pad" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <div className="section-title">Your exports</div>
                <div className="spacer" />
                <span className="hint">{files.length} file{files.length === 1 ? "" : "s"}</span>
              </div>
              <span className="hint">Click to save the MP4 to your Downloads folder.</span>
              {files.map((f) => (
                <a key={f.name} href={f.url} download={f.name} className="btn small" style={{ justifyContent: "space-between" }} title={`Save ${f.name}`}>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</span>
                  <span style={{ flexShrink: 0, opacity: 0.8 }}>{fmtBytes(f.size)} ⬇</span>
                </a>
              ))}
            </div>
          ) : null}
        </div>

        {clip && style ? (
          <PreviewColumn
            project={project}
            clip={clip}
            style={style}
            onClipChange={patchClip}
            onSaveStyle={() => saveStyle(style)}
          />
        ) : (
          <div className="card empty" style={{ padding: 60 }}>
            {analyzing ? (
              <><span className="spinner" /> Transcribing and finding clips…</>
            ) : (
              "Select a clip to start editing."
            )}
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="tabs" role="tablist" aria-label="Editor panels">
            <button
              role="tab"
              aria-selected={tab === "captions"}
              className={"tab" + (tab === "captions" ? " active" : "")}
              onClick={() => setTab("captions")}
            >
              Captions
            </button>
            <button
              role="tab"
              aria-selected={tab === "video"}
              className={"tab" + (tab === "video" ? " active" : "")}
              onClick={() => setTab("video")}
            >
              Video & audio
            </button>
          </div>
          {tab === "captions" && clip && style ? (
            <StylePanel
              style={style}
              onChange={queueStyle}
              builtin={builtin}
              currentPreset={presetName}
            />
          ) : (
            <>
              <SettingsPanel
                project={project}
                onProject={setProject}
                onApplyBrand={applyBrand}
                onReanalyze={async () => {
                  try {
                    const { job_id } = await api.reanalyze(projectId, {});
                    app.watchJob(job_id, () => load());
                    app.toast("Re-detecting clips…", "info");
                  } catch (e) {
                    app.toast((e as Error).message, "err");
                  }
                }}
              />
              {clip?.crop_plan && (
                <div className="card pad">
                  <div className="section-title" style={{ marginBottom: 6 }}>Reframe plan</div>
                  <div className="hint">
                    {clip.crop_plan.faces_found} face samples · {clip.crop_plan.keyframes.length} keyframes ·{" "}
                    {clip.crop_plan.wide ? "wide shot detected (try blur)" : "speaker centred"}
                  </div>
                </div>
              )}
            </>
          )}
          {clip && <SocialCard projectId={projectId} clip={clip} />}
        </div>
      </div>
    </div>
  );
}
