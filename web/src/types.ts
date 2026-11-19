export type Word = { text: string; start: number; end: number; emoji?: string; prob?: number };

export type CaptionStyle = {
  preset: string;
  animation: "pop" | "karaoke" | "typewriter" | "fade" | "none";
  font: string;
  font_size: number;
  uppercase: boolean;
  primary_color: string;
  highlight_color: string;
  outline_color: string;
  outline_width: number;
  shadow: number;
  shadow_color: string;
  glow: number;
  glow_color: string;
  blur_edges: number;
  position: "top" | "middle" | "bottom";
  offset: number | null;
  words_per_line: number;
  max_chars: number;
  line_spacing: number;
  letter_spacing: number;
  box: boolean;
  box_color: string;
  box_opacity: number;
  box_padding: number;
  emoji: boolean;
  emoji_every: number;
  emoji_scale: number;
  word_hold: number;
  pop_scale: number;
  pop_ms: number;
  stroke_style: number;
  margin_side: number;
};

export type CropPlan = {
  fps: number;
  keyframes: [number, number, number][];
  wide: boolean;
  faces_found: number;
  crop: { w: number; h: number };
  source: { width: number; height: number };
};

export type Clip = {
  id: string;
  start: number;
  end: number;
  title: string;
  score: number | null;
  reason?: string;
  words: Word[];
  words_override?: Word[] | null;
  style?: CaptionStyle | null;
  style_preset?: string;
  crop_plan?: CropPlan | null;
  thumb?: string | null;
  wide?: boolean;
  provider?: string;
  export?: { file: string; url: string; duration: number; size: number };
};

export type SfxItem = { id: string; path: string; name: string; at: number; volume: number; duration: number };

export type ProjectOptions = {
  reframe: "face" | "center";
  platform: string;
  aspect: string;
  quality: string;
  layout: "standard" | "gameplay_top" | "gameplay_full";
  gameplay_path: string | null;
  sfx: SfxItem[];
  blur_background: boolean;
  remove_silences: boolean;
  remove_fillers: boolean;
  voice_boost: boolean;
  music_path: string | null;
  music_volume: number;
  music_fade: number;
  auto_duck: boolean;
  watermark_path: string | null;
  watermark_width: number;
  watermark_opacity: number;
  watermark_position: string;
  clip_min: number;
  clip_max: number;
  clip_count: number;
  provider: "auto" | "llm" | "heuristic";
};

export type Project = {
  id: string;
  title: string;
  source: string;
  created: number;
  media: {
    duration: number;
    width: number;
    height: number;
    fps: number;
    has_audio: boolean;
  };
  transcript: {
    language: string;
    duration: number;
    words: Word[];
    text: string;
    segments: { start: number; end: number; text: string }[];
  } | null;
  clips: Clip[];
  options: ProjectOptions;
  style?: CaptionStyle | null;
  status: string;
  poster: string | null;
  analysis_provider?: string;
};

export type Job = {
  id: string;
  kind: string;
  title: string;
  status: "queued" | "running" | "done" | "error";
  stage: string;
  progress: number;
  message: string;
  error?: string | null;
  project_id?: string;
  clip_id?: string;
  files?: { name: string; url: string; clip_id: string; size?: number; error?: string }[];
  clip_count?: number;
  provider?: string;
};

export type SavedPreset = { id: string; name: string; style: CaptionStyle; created: number };

export type FontInfo = { family: string; file: string | null; uploaded: boolean };

export type PlatformPreset = {
  label: string;
  aspect: string;
  min_len: number;
  max_len: number;
  safe: { top: number; bottom: number; side: number };
  note: string;
};

export type PlatformCatalogue = {
  platforms: Record<string, PlatformPreset>;
  aspects: Record<string, { label: string; width: number; height: number }>;
  quality: Record<string, { label: string; crf: number; preset: string; audio: string }>;
};

export type BrandKit = {
  name: string;
  logo_path: string | null;
  colors: string[];
  fonts: string[];
  watermark: { position: string; width: number; opacity: number };
};

export type SocialCopy = { title: string; caption: string; hashtags: string[]; provider: string };

export type HistoryInfo = { can_undo: boolean; can_redo: boolean; undo_label: string | null };

export type Health = {
  ok: boolean;
  platforms?: PlatformCatalogue;
  /** False when the data folder is not on a mounted volume (lost on restart). */
  storage?: { durable: boolean; path: string; free_gb: number | null };
  ffmpeg: string;
  ytdlp: boolean;
  presets: string[];
  settings: {
    llm_base_url: string;
    llm_model: string;
    llm_configured: boolean;
    whisper_model: string;
    language: string;
  };
};
