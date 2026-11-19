import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../state";
import { fmt, timeAgo } from "../lib";
import type { Job } from "../types";

type ProjectRow = {
  id: string;
  title: string;
  created: number;
  duration: number;
  clip_count: number;
  poster: string | null;
  status: string;
};

export function Home() {
  const app = useApp();
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [drag, setDrag] = useState(false);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [ytUrl, setYtUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [llmKey, setLlmKey] = useState("");
  const [llmModel, setLlmModel] = useState(app.health?.settings.llm_model ?? "gpt-4o-mini");
  const [llmBase, setLlmBase] = useState(app.health?.settings.llm_base_url ?? "https://api.openai.com/v1");
  const [whisper, setWhisper] = useState(app.health?.settings.whisper_model ?? "base");
  const fileRef = useRef<HTMLInputElement>(null);
  const uploadRef = useRef<{ cancel: () => void } | null>(null);
  const [phase, setPhase] = useState<string>("");
  const [projectError, setProjectError] = useState<string | null>(null);
  const [brandOpen, setBrandOpen] = useState(false);
  const [brandName, setBrandName] = useState("");
  const [brandColors, setBrandColors] = useState<string[]>([]);
  const [brandFont, setBrandFont] = useState("");
  const [brandLogo, setBrandLogo] = useState<string | null>(null);
  const [brandBusy, setBrandBusy] = useState(false);
  const logoRef = useRef<HTMLInputElement>(null);

  const refresh = () =>
    api.projects()
      .then((r) => { setProjects(r.projects); setProjectError(null); })
      .catch((e) => setProjectError(e instanceof Error ? e.message : "Could not load projects"));

  useEffect(() => {
    refresh();
  }, []);

  useEffect(() => {
    if (app.activeJobs.length === 0) refresh();
  }, [app.activeJobs.length]);

  useEffect(() => {
    if (app.brand) {
      setBrandName(app.brand.name);
      setBrandColors(app.brand.colors.length ? app.brand.colors : ["#FFFFFF", "#FFE600"]);
      setBrandFont(app.brand.fonts[0] ?? "");
      setBrandLogo(app.brand.logo_path);
    }
  }, [app.brand]);

  useEffect(() => {
    if (app.health) {
      setLlmModel(app.health.settings.llm_model);
      setLlmBase(app.health.settings.llm_base_url);
      setWhisper(app.health.settings.whisper_model);
    }
  }, [app.health]);

  // follow YouTube downloads through to the editor
  useEffect(() => {
    const dl = app.jobs.find((j: Job) => j.kind === "download" && j.status === "done" && j.project_id);
    if (dl?.project_id) app.openProject(dl.project_id);
  }, [app.jobs, app]);

  const MAX_UPLOAD = 2 * 1024 ** 3;
  const VIDEO_RE = /\.(mp4|mov|mkv|webm|m4v|avi|mts|m2ts)$/i;

  const startUpload = async (file: File) => {
    if (!VIDEO_RE.test(file.name)) {
      app.toast("That is not a video file. Use MP4, MOV, MKV, WEBM, M4V or AVI.", "err");
      return;
    }
    if (file.size === 0) {
      app.toast("That file is empty.", "err");
      return;
    }
    if (file.size > MAX_UPLOAD) {
      app.toast(`That file is ${(file.size / 1024 ** 3).toFixed(1)} GB. The limit is 2 GB.`, "err");
      return;
    }
    setBusy(true);
    setUploadPct(0);
    setPhase("Uploading");
    const handle = api.uploadVideo(file, file.name.replace(/\.[^.]+$/, ""), setUploadPct);
    uploadRef.current = handle;
    try {
      const res = await handle.promise;
      setPhase("Analysing");
      setUploadPct(null);
      app.watchJob(res.job_id, () => {
        refresh();
      });
      app.openProject(res.project_id);
    } catch (e) {
      app.toast((e as Error).message, "err");
    } finally {
      uploadRef.current = null;
      setBusy(false);
      setUploadPct(null);
      setPhase("");
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const cancelUpload = () => {
    uploadRef.current?.cancel();
    app.toast("Upload cancelled.", "info");
  };

  const startYoutube = async () => {
    if (!ytUrl.trim()) return;
    if (!/^https?:\/\//i.test(ytUrl.trim())) {
      app.toast("Paste a full link starting with http:// or https://", "err");
      return;
    }
    setBusy(true);
    try {
      const { job_id } = await api.youtube(ytUrl.trim());
      app.watchJob(job_id, (job) => {
        if (job.project_id) app.openProject(job.project_id);
        else app.toast("Download finished but analysis has not started yet.", "info");
      });
      app.toast("Downloading video…", "info");
      setYtUrl("");
    } catch (e) {
      app.toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  };

  const saveSettings = async () => {
    try {
      await api.saveSettings({
        llm_api_key: llmKey || undefined,
        llm_model: llmModel,
        llm_base_url: llmBase,
        whisper_model: whisper,
      });
      const h = await api.health();
      setLlmKey("");
      setSettingsOpen(false);
      app.toast("Settings saved", "ok");
      void h;
    } catch (e) {
      app.toast((e as Error).message, "err");
    }
  };

  const deleteProject = async (id: string) => {
    if (!confirm("Delete this project and its exports?")) return;
    await api.deleteProject(id);
    refresh();
  };

  return (
    <div className="home">
      <div className="topbar">
        <div className="brand">
          <div className="logo">◧</div> ClipForge
        </div>
        <div className="spacer" />
        <span className="chip">
          <span className={"dot " + (app.health ? "" : "warn")} />
          {app.health ? "Engine ready" : "Connecting…"}
        </span>
        <span className="chip" title={app.health?.ffmpeg ?? ""}>
          {app.health?.ytdlp ? "yt-dlp ✓" : "yt-dlp missing"}
        </span>
        <span className="chip" title={app.health?.settings.llm_model}>
          {app.health?.settings.llm_configured ? "LLM ✓" : "LLM not set"}
        </span>
        <a className="btn small ghost" href="/pricing.html" style={{ textDecoration: "none" }}>Pricing</a>
        <button className="btn small" onClick={() => setBrandOpen(true)}>Brand kit</button>
        <button className="btn small" onClick={() => setSettingsOpen(true)}>Settings</button>
      </div>

      <div className="hero">
        <h1>
          Long videos in.<br />
          <span>Scroll-stopping vertical clips out.</span>
        </h1>
        <p className="lead">
          Word-level captions you control down to the millisecond, face-tracked 9:16 framing, and five
          ready-made caption looks. Everything runs on your machine.
        </p>

        {app.health?.storage && !app.health.storage.durable && (
          <div className="banner warn" role="status">
            <strong>This server has no permanent disk.</strong> Projects, exports and downloads are
            erased whenever it restarts or redeploys. Attach a disk to keep your work.
          </div>
        )}

        <div
          className={"dropzone" + (drag ? " over" : "")}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDrag(false);
          }}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            if (busy) {
              app.toast("An upload is already running. Cancel it first.", "info");
              return;
            }
            const files = e.dataTransfer.files;
            if (files && files.length > 1) app.toast("Only the first file was used.", "info");
            const f = files?.[0];
            if (f) startUpload(f);
          }}
        >
          <div className="icon">🎬</div>
          <div style={{ fontWeight: 650, marginBottom: 4 }}>Drop a video here</div>
          <div className="hint" style={{ marginBottom: 14 }}>MP4, MOV, MKV, WEBM — up to 2 GB</div>
          <input
            ref={fileRef}
            type="file"
            accept="video/mp4,video/quicktime,video/webm,video/x-matroska,.mp4,.mov,.mkv,.webm,.m4v,.avi,.mts,.m2ts"
            style={{ display: "none" }}
            onChange={(e) => e.target.files?.[0] && startUpload(e.target.files[0])}
          />
          <button className="btn primary" onClick={() => fileRef.current?.click()} disabled={busy}>
            {busy ? <><span className="spinner" /> {phase || "Working"}…</> : "Choose video"}
          </button>
          {uploadPct != null && (
            <>
              <div className="progress" style={{ marginTop: 14 }} role="progressbar" aria-valuenow={Math.round(uploadPct * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Upload progress">
                <div style={{ width: `${uploadPct * 100}%` }} />
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "center", marginTop: 8 }}>
                <span className="hint">Uploading {Math.round(uploadPct * 100)}%</span>
                <button className="btn small danger" onClick={cancelUpload}>Cancel</button>
              </div>
            </>
          )}
        </div>

        <div className="or">or import from a link</div>
        <div className="yt-row">
          <input
            type="url"
            placeholder="https://www.youtube.com/watch?v=…"
            value={ytUrl}
            aria-label="Video link"
            onChange={(e) => setYtUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && startYoutube()}
          />
          <button className="btn" onClick={startYoutube} disabled={busy || !ytUrl.trim()}>
            Import
          </button>
        </div>
        <div className="hint" style={{ marginTop: 8 }}>
          Only download videos you have the right to use.
        </div>

        <div className="feature-grid">
          {[
            ["Word-level captions", "Pop, karaoke, typewriter and fade — with your fonts and colours."],
            ["Face-tracked framing", "MediaPipe follows the speaker; blur fallback for wide shots."],
            ["Smart cuts", "Sentence-aligned clips scored for hooks, 20–60 seconds."],
            ["One-click export", "1080×1920 H.264 with optional music ducking and a watermark."],
          ].map(([h, p]) => (
            <div className="feature" key={h}>
              <h4>{h}</h4>
              <p>{p}</p>
            </div>
          ))}
        </div>
      </div>

      {app.activeJobs.length > 0 && (
        <div className="card pad" style={{ maxWidth: 900, margin: "34px auto 0", display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="section-title">Working</div>
          <div className="job-list">
            {app.activeJobs.map((j) => (
              <div key={j.id} style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
                  <span>{j.title || j.kind} · {j.stage}{j.message ? <span className="hint"> — {j.message}</span> : null}</span>
                  <span className="hint">{Math.round(j.progress * 100)}%</span>
                </div>
                <div className="progress"><div style={{ width: `${j.progress * 100}%` }} /></div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ maxWidth: 900, margin: "34px auto 0" }}>
        <div style={{ display: "flex", alignItems: "center", marginBottom: 10 }}>
          <div className="section-title">Recent projects</div>
          <div className="spacer" />
          <button className="btn small ghost" onClick={refresh}>Refresh</button>
        </div>
        {projectError ? (
          <div className="card empty" style={{ color: "var(--bad)" }}>
            {projectError}
            <div style={{ marginTop: 10 }}>
              <button className="btn small" onClick={refresh}>Retry</button>
            </div>
          </div>
        ) : projects.length === 0 ? (
          <div className="card empty">Nothing here yet — your first upload will appear here.</div>
        ) : (
          <div className="projects">
            {projects.map((p) => (
              <div key={p.id} className="project-row">
                <div
                  className="thumb"
                  style={p.poster ? { backgroundImage: `url(${p.poster})` } : undefined}
                  onClick={() => app.openProject(p.id)}
                />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {p.title}
                  </div>
                  <div className="hint" style={{ marginTop: 3 }}>
                    {fmt(p.duration || 0)} · {p.clip_count} clips · {timeAgo(p.created)}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 6 }}>
                  <button className="btn small" onClick={() => app.openProject(p.id)}>Open</button>
                  <button className="btn small ghost danger" onClick={() => deleteProject(p.id)}>Delete</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {brandOpen && (
        <div className="modal-bg" onClick={() => setBrandOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Brand kit">
            <h3>Brand kit</h3>
            <p className="hint" style={{ marginBottom: 14 }}>
              Save your logo, colours and font once. Any project can apply them from the Brand tab.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div className="field">
                <label htmlFor="brand-name">Brand name</label>
                <input id="brand-name" value={brandName} onChange={(e) => setBrandName(e.target.value)} placeholder="Your studio or channel" />
              </div>
              <div className="field">
                <label>Logo / watermark</label>
                <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <span className="hint" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {brandLogo ? brandLogo.split("/").pop() : "No logo uploaded"}
                  </span>
                  <input
                    ref={logoRef}
                    type="file"
                    accept=".png,.jpg,.jpeg,.webp"
                    style={{ display: "none" }}
                    onChange={async (e) => {
                      const f = e.target.files?.[0];
                      if (!f) return;
                      setBrandBusy(true);
                      try {
                        const info = await api.uploadAsset("watermark", f);
                        setBrandLogo(info.path);
                      } catch (err) {
                        app.toast((err as Error).message, "err");
                      } finally {
                        setBrandBusy(false);
                      }
                    }}
                  />
                  <button className="btn small" onClick={() => logoRef.current?.click()} disabled={brandBusy}>
                    {brandBusy ? <span className="spinner" /> : "Upload"}
                  </button>
                </div>
              </div>
              <div className="row2">
                {[0, 1].map((i) => (
                  <div className="field" key={i}>
                    <label htmlFor={`brand-color-${i}`}>{i === 0 ? "Text colour" : "Highlight colour"}</label>
                    <div className="color-row">
                      <input
                        id={`brand-color-${i}`}
                        type="color"
                        value={brandColors[i] ?? (i ? "#FFE600" : "#FFFFFF")}
                        onChange={(e) => {
                          const next = [...brandColors];
                          while (next.length <= i) next.push(i ? "#FFE600" : "#FFFFFF");
                          next[i] = e.target.value;
                          setBrandColors(next);
                        }}
                      />
                      <span className="hint mono">{brandColors[i] ?? (i ? "#FFE600" : "#FFFFFF")}</span>
                    </div>
                  </div>
                ))}
              </div>
              <div className="field">
                <label htmlFor="brand-font">Caption font</label>
                <select id="brand-font" value={brandFont} onChange={(e) => setBrandFont(e.target.value)}>
                  <option value="">Keep each clip's font</option>
                  {app.fonts.map((f) => (
                    <option key={f.family} value={f.family}>{f.family}</option>
                  ))}
                </select>
              </div>
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 6 }}>
                <button className="btn ghost" onClick={() => setBrandOpen(false)}>Cancel</button>
                <button
                  className="btn primary"
                  disabled={brandBusy}
                  onClick={async () => {
                    setBrandBusy(true);
                    try {
                      await api.saveBrand({
                        name: brandName,
                        logo_path: brandLogo,
                        colors: brandColors.slice(0, 2),
                        fonts: brandFont ? [brandFont] : [],
                      });
                      await app.refreshBrand();
                      setBrandOpen(false);
                      app.toast("Brand kit saved", "ok");
                    } catch (err) {
                      app.toast((err as Error).message, "err");
                    } finally {
                      setBrandBusy(false);
                    }
                  }}
                >
                  Save brand kit
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {settingsOpen && (
        <div className="modal-bg" onClick={() => setSettingsOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>Settings</h3>
            <p className="hint" style={{ marginBottom: 16 }}>
              The LLM is optional. Without a key, clip detection uses the built-in offline scorer.
              Keys stay on this machine in the project data folder.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div className="field">
                <label>LLM API key</label>
                <input
                  type="password"
                  placeholder={app.health?.settings.llm_configured ? "•••• saved — enter to replace" : "sk-…"}
                  value={llmKey}
                  onChange={(e) => setLlmKey(e.target.value)}
                />
              </div>
              <div className="field">
                <label>Model</label>
                <input value={llmModel} onChange={(e) => setLlmModel(e.target.value)} />
              </div>
              <div className="field">
                <label>Base URL <span className="hint">OpenAI-compatible</span></label>
                <input value={llmBase} onChange={(e) => setLlmBase(e.target.value)} />
              </div>
              <div className="field">
                <label>Whisper model</label>
                <select value={whisper} onChange={(e) => setWhisper(e.target.value)}>
                  <option value="tiny">tiny — fastest, rough</option>
                  <option value="base">base — good default</option>
                  <option value="small">small — better accuracy</option>
                  <option value="medium">medium — slow on CPU</option>
                  <option value="large-v3">large-v3 — best, very slow</option>
                </select>
              </div>
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 6 }}>
                <button className="btn ghost" onClick={() => setSettingsOpen(false)}>Cancel</button>
                <button className="btn primary" onClick={saveSettings}>Save settings</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
