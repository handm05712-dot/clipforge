import type {
  BrandKit, FontInfo, Health, HistoryInfo, Job, PlatformCatalogue, Project,
  SavedPreset, SocialCopy,
} from "./types";

const base = "";

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = body.detail || body.message || msg;
    } catch {
      /* ignore */
    }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json() as Promise<T>;
}

function json(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export const api = {
  health: () => fetch(base + "/api/health").then((r) => j<Health>(r)),

  settings: () => fetch(base + "/api/settings").then((r) => j<Health["settings"]>(r)),
  saveSettings: (patch: Partial<Health["settings"]> & { llm_api_key?: string }) =>
    fetch(base + "/api/settings", { ...json(patch), method: "PUT" }).then((r) => j<Health["settings"]>(r)),

  presets: () => fetch(base + "/api/presets").then((r) => j<{ builtin: import("./types").CaptionStyle[]; saved: SavedPreset[] }>(r)),
  savePreset: (name: string, style: unknown) =>
    fetch(base + "/api/presets", json({ name, style })).then((r) => j<{ saved: SavedPreset[] }>(r)),
  deletePreset: (id: string) =>
    fetch(base + "/api/presets/" + id, { method: "DELETE" }).then((r) => j<{ saved: SavedPreset[] }>(r)),

  fonts: () => fetch(base + "/api/fonts").then((r) => j<{ fonts: FontInfo[] }>(r)),
  uploadFont: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch(base + "/api/fonts/upload", { method: "POST", body: fd }).then((r) => j<FontInfo>(r));
  },
  googleFont: (family: string) =>
    fetch(base + "/api/fonts/google", json({ family })).then((r) => j<FontInfo>(r)),
  deleteFont: (file: string) =>
    fetch(base + "/api/fonts/" + encodeURIComponent(file), { method: "DELETE" }).then((r) => j<{ deleted: boolean }>(r)),

  uploadAsset: (kind: "music" | "watermark", file: File) => {
    const fd = new FormData();
    fd.append("kind", kind);
    fd.append("file", file);
    return fetch(base + "/api/assets", { method: "POST", body: fd }).then((r) =>
      j<{ kind: string; path: string; name: string; size: number }>(r),
    );
  },

  /** Upload with progress; returns a handle so the caller can cancel. */
  uploadVideo: (file: File, title: string, onProgress?: (p: number) => void) => {
    const xhr = new XMLHttpRequest();
    const promise = new Promise<{ project_id: string; job_id: string }>((resolve, reject) => {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("title", title);
      xhr.open("POST", base + "/api/projects/upload");
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
      };
      xhr.onload = () => {
        let body: { detail?: string; project_id?: string; job_id?: string } = {};
        try {
          body = JSON.parse(xhr.responseText || "{}");
        } catch {
          reject(new Error(`Upload failed (server said ${xhr.status})`));
          return;
        }
        if (xhr.status >= 300) reject(new Error(body.detail || `Upload failed (${xhr.status})`));
        else resolve(body as { project_id: string; job_id: string });
      };
      xhr.onerror = () => reject(new Error("Upload failed — check your connection and try again."));
      xhr.onabort = () => reject(new Error("Upload cancelled."));
      xhr.ontimeout = () => reject(new Error("Upload timed out. Try a smaller file."));
      xhr.send(fd);
    });
    return { promise, cancel: () => xhr.abort() };
  },

  youtube: (url: string, title?: string) =>
    fetch(base + "/api/projects/youtube", json({ url, title })).then((r) => j<{ job_id: string }>(r)),

  projects: () =>
    fetch(base + "/api/projects").then((r) =>
      j<{ projects: (Pick<Project, "id" | "title" | "created" | "source"> & { duration: number; clip_count: number; poster: string | null; status: string })[] }>(r),
    ),
  project: (id: string) => fetch(base + "/api/projects/" + id).then((r) => j<Project>(r)),
  deleteProject: (id: string) =>
    fetch(base + "/api/projects/" + id, { method: "DELETE" }).then((r) => j<{ ok: boolean }>(r)),
  patchProject: (id: string, patch: unknown) =>
    fetch(base + "/api/projects/" + id, { ...json(patch), method: "PATCH" }).then((r) => j<Project>(r)),
  reanalyze: (id: string, opts: unknown) =>
    fetch(base + "/api/projects/" + id + "/reanalyze", json(opts)).then((r) => j<{ job_id: string }>(r)),
  redetect: (id: string, opts: unknown) =>
    fetch(base + "/api/projects/" + id + "/detect", json(opts)).then((r) => j<{ clips: import("./types").Clip[] }>(r)),

  patchClip: (pid: string, cid: string, patch: unknown) =>
    fetch(base + "/api/projects/" + pid + "/clips/" + cid, { ...json(patch), method: "PATCH" }).then(
      (r) => j<import("./types").Clip>(r),
    ),
  addClip: (pid: string, start: number, end: number) =>
    fetch(base + "/api/projects/" + pid + "/clips", json({ start, end })).then((r) => j<import("./types").Clip>(r)),
  deleteClip: (pid: string, cid: string) =>
    fetch(base + "/api/projects/" + pid + "/clips/" + cid, { method: "DELETE" }).then((r) => j<{ ok: boolean }>(r)),

  previewFrame: async (pid: string, cid: string, style: unknown, at: number) => {
    const res = await fetch(base + `/api/projects/${pid}/clips/${cid}/preview-frame`, json({ style, at }));
    if (!res.ok) throw new Error("Preview failed");
    return URL.createObjectURL(await res.blob());
  },

  exportClip: (pid: string, cid: string, style?: unknown) =>
    fetch(base + `/api/projects/${pid}/clips/${cid}/export`, json({ style })).then((r) => j<{ job_id: string }>(r)),
  exportBatch: (pid: string, clip_ids: string[]) =>
    fetch(base + `/api/projects/${pid}/export`, json({ clip_ids })).then((r) => j<{ job_id: string }>(r)),

  exports: (pid: string) =>
    fetch(base + `/api/projects/${pid}/exports`).then(
      (r) => j<{ files: { name: string; url: string; size: number; created: number; title?: string }[] }>(r),
    ),

  platforms: () => fetch(base + "/api/platforms").then((r) => j<PlatformCatalogue>(r)),
  brand: () => fetch(base + "/api/brand").then((r) => j<BrandKit>(r)),
  saveBrand: (patch: Partial<BrandKit>) =>
    fetch(base + "/api/brand", { ...json(patch), method: "PUT" }).then((r) => j<BrandKit>(r)),
  applyBrand: (pid: string) =>
    fetch(base + "/api/projects/" + pid + "/brand", { method: "POST" }).then((r) => j<Project>(r)),
  history: (pid: string) =>
    fetch(base + "/api/projects/" + pid + "/history").then((r) => j<HistoryInfo>(r)),
  undo: (pid: string) =>
    fetch(base + "/api/projects/" + pid + "/history/undo", { method: "POST" }).then(
      (r) => j<{ project: Project } & HistoryInfo>(r),
    ),
  redo: (pid: string) =>
    fetch(base + "/api/projects/" + pid + "/history/redo", { method: "POST" }).then(
      (r) => j<{ project: Project } & HistoryInfo>(r),
    ),
  plan: (pid: string, cid: string) =>
    fetch(base + `/api/projects/${pid}/clips/${cid}/plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }).then((r) =>
      j<{ keep_ranges: number[][]; raw_duration: number; duration: number; removed: number; words: import("./types").Word[] }>(r),
    ),

  social: (pid: string, cid: string, platform?: string) =>
    fetch(base + `/api/projects/${pid}/clips/${cid}/social`, json({ platform })).then(
      (r) => j<SocialCopy>(r),
    ),

  jobs: () => fetch(base + "/api/jobs").then((r) => j<{ jobs: Job[] }>(r)),
  job: (id: string) => fetch(base + "/api/jobs/" + id).then((r) => j<Job>(r)),
};

export const exportUrl = (pid: string, name: string) =>
  `/api/projects/${pid}/exports/${encodeURIComponent(name)}`;
