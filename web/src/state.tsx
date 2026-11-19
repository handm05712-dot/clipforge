import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { ensureFontFaces } from "./lib";
import type { BrandKit, FontInfo, Health, Job, PlatformCatalogue, SavedPreset } from "./types";

export type Toast = { id: number; msg: string; kind: "info" | "ok" | "err" };

type Route = { page: "home" | "editor"; projectId?: string };

export type AppState = {
  health: Health | null;
  healthError: string | null;
  platforms: PlatformCatalogue | null;
  brand: BrandKit | null;
  refreshBrand: () => Promise<void>;
  fonts: FontInfo[];
  presets: SavedPreset[];
  refreshFonts: () => Promise<void>;
  refreshPresets: () => Promise<void>;
  jobs: Job[];
  watchJob: (id: string, onDone?: (job: Job) => void) => void;
  toast: (msg: string, kind?: Toast["kind"]) => void;
  toasts: Toast[];
  openProject: (id: string) => void;
  goHome: () => void;
  route: Route;
  activeJobs: Job[];
};

export const AppCtx = createContext<AppState | null>(null);

export function useApp(): AppState {
  const ctx = useContext(AppCtx);
  if (!ctx) throw new Error("AppCtx missing");
  return ctx;
}

function readRoute(): Route {
  const m = location.hash.match(/^#\/p\/(\w+)/);
  return m ? { page: "editor", projectId: m[1] } : { page: "home" };
}

export function useAppState(): AppState {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [platforms, setPlatforms] = useState<PlatformCatalogue | null>(null);
  const [brand, setBrand] = useState<BrandKit | null>(null);
  const [fonts, setFonts] = useState<FontInfo[]>([]);
  const [presets, setPresets] = useState<SavedPreset[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [route, setRoute] = useState<Route>(readRoute);
  const watchers = useRef<Map<string, (j: Job) => void>>(new Map());
  const seen = useRef<Set<string>>(new Set());

  const refreshFonts = useCallback(async () => {
    try {
      const list = (await api.fonts()).fonts;
      setFonts(list);
      ensureFontFaces(list);
    } catch {
      /* server may still be starting */
    }
  }, []);

  const refreshPresets = useCallback(async () => {
    try {
      setPresets((await api.presets()).saved);
    } catch {
      /* ignore */
    }
  }, []);

  const refreshBrand = useCallback(async () => {
    try {
      setBrand(await api.brand());
    } catch {
      /* ignore */
    }
  }, []);

  const toast = useCallback((msg: string, kind: Toast["kind"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, msg, kind }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "err" ? 7000 : 4200);
  }, []);

  // Poll only when there might be something to see, and back off when the
  // editor is open with nothing running. A fixed 1.2s poll of /api/jobs even
  // while idle was adding constant work and request churn.
  const hasActive = useRef(false);
  const poll = useCallback(async () => {
    try {
      const r = await api.jobs();
      hasActive.current = r.jobs.some((j) => j.status === "running" || j.status === "queued");
      setJobs((prev) =>
        prev.length === r.jobs.length &&
        prev.every((p, i) => {
          const n = r.jobs[i];
          return (
            p.id === n.id &&
            p.status === n.status &&
            p.progress === n.progress &&
            p.stage === n.stage
          );
        })
          ? prev
          : r.jobs,
      );
      for (const job of r.jobs) {
        const done = job.status === "done" || job.status === "error";
        const cb = watchers.current.get(job.id);
        if (cb && done) {
          watchers.current.delete(job.id);
          cb(job);
        }
        if (done && !seen.current.has(job.id)) {
          seen.current.add(job.id);
          if (job.status === "error") {
            toast(`${job.title || "Job"} failed: ${job.error || "unknown error"}`, "err");
          } else if (job.kind === "export" || job.kind === "batch") {
            toast("Export finished — your file is in the download list.", "ok");
          } else if (job.kind === "analyze") {
            toast(`Analysis finished: ${job.clip_count ?? 0} clips ready.`, "ok");
          }
        }
      }
    } catch {
      /* ignore */
    }
  }, [toast]);

  useEffect(() => {
    api.health()
      .then((h) => {
        setHealth(h);
        setHealthError(null);
        if (h.platforms) setPlatforms(h.platforms);
      })
      .catch((e) => {
        setHealthError(e instanceof Error ? e.message : "Cannot reach the ClipForge engine");
      });
    api.platforms().then(setPlatforms).catch(() => undefined);
    refreshFonts();
    refreshPresets();
    refreshBrand();
    poll();
    // One self-rescheduling timer: 1.2s while a job runs, 6s when nothing is
    // happening. A fixed 1.2s poll even while idle kept the CPU busy for nothing.
    let timer = 0;
    const tick = () => {
      poll();
      timer = window.setTimeout(tick, hasActive.current ? 1200 : 6000);
    };
    timer = window.setTimeout(tick, hasActive.current ? 1200 : 6000);

    const onHash = () => setRoute(readRoute());
    // a hidden tab does not need to poll; keeps CPU and memory flat while away
    const onVisibility = () => {
      window.clearTimeout(timer);
      if (!document.hidden) {
        poll();
        timer = window.setTimeout(tick, hasActive.current ? 1200 : 6000);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("hashchange", onHash);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("hashchange", onHash);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [poll, refreshFonts, refreshPresets, refreshBrand]);

  const watchJob = useCallback((id: string, onDone?: (job: Job) => void) => {
    if (onDone) watchers.current.set(id, onDone);
  }, []);

  useEffect(() => {
    if (healthError) toast("Backend unreachable — retrying…", "err");
  }, [healthError, toast]);

  const openProject = useCallback((id: string) => {
    location.hash = `#/p/${id}`;
    setRoute({ page: "editor", projectId: id });
  }, []);

  const goHome = useCallback(() => {
    location.hash = "#/";
    setRoute({ page: "home" });
  }, []);

  const activeJobs = useMemo(
    () => jobs.filter((j) => j.status === "running" || j.status === "queued"),
    [jobs],
  );

  return useMemo(
    () => ({
      health, healthError, platforms, brand, refreshBrand, fonts, presets,
      refreshFonts, refreshPresets, jobs, watchJob, toast,
      toasts, openProject, goHome, route, activeJobs,
    }),
    [health, healthError, platforms, brand, refreshBrand, fonts, presets, refreshFonts,
      refreshPresets, jobs, watchJob, toast, toasts, openProject, goHome, route, activeJobs],
  );
}
