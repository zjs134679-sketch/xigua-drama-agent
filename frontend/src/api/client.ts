const API = "/api";

export interface ComplianceHit {
  word: string;
  level: string;
  category: string;
}
export interface ComplianceResult {
  level: "pass" | "yellow" | "red";
  blocked: boolean;
  hits: ComplianceHit[];
}

export async function checkCompliance(text: string): Promise<ComplianceResult> {
  const r = await fetch(`${API}/compliance/check`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  return r.json();
}

export async function getComputeHealth(): Promise<{ type: string; base_url: string; online: boolean }> {
  const r = await fetch(`${API}/compute/health`);
  return r.json();
}

export interface ScriptDraftResponse {
  status: number;
  script?: string;
  warn?: boolean;
  level?: string;
  message?: string;
  hits?: ComplianceHit[];
  violationCount?: number;
  banned?: boolean;
}

export async function generateScriptDraft(content: string, username = "local"): Promise<ScriptDraftResponse> {
  const r = await fetch(`${API}/script/draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, username }),
  });
  const data = await r.json();
  if (r.ok) {
    return { status: r.status, script: data.script_content, warn: data.warn, hits: data.hits };
  }
  const detail = data.detail;
  if (detail && typeof detail === "object") {
    return {
      status: r.status,
      level: detail.level,
      message: detail.message,
      hits: detail.hits,
      violationCount: detail.violation_count,
      banned: detail.banned,
    };
  }
  return { status: r.status, message: typeof detail === "string" ? detail : "生成失败" };
}

export interface ComplianceSyncResult {
  synced: boolean;
  unchanged: boolean;
  version: string | null;
}

export interface ComplianceStatus {
  username: string;
  violation_count: number;
  banned: boolean;
  banned_reason: string | null;
}

export function syncCompliance(): Promise<ComplianceSyncResult> {
  return jsonRequest("/compliance/sync", { method: "POST" });
}

export function getComplianceStatus(username = "local"): Promise<ComplianceStatus> {
  return jsonRequest(`/compliance/status?username=${encodeURIComponent(username)}`);
}

export interface ArtStyle {
  id: number;
  name: string;
  prompt_suffix: string;
  lora: string | null;
  thumbnail: string | null;
  sort_order: number;
}

export interface Project {
  id: number;
  title: string;
}

export interface EpisodeSummary {
  id: number;
  drama_id: number;
  episode_number: number;
  title: string;
  status: string;
}

export interface CharacterAsset {
  id: number;
  name: string;
  role: string | null;
  appearance: string | null;
  image_url: string | null;
}

export interface AssetGenerationResult {
  status?: string;
  asset_id?: number;
  image_url?: string | null;
  local_path?: string | null;
  warn?: boolean;
  warn_hits?: ComplianceHit[];
  blocked?: boolean;
  message?: string;
}

async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${url}`, init);
  const data = await response.json();
  if (!response.ok) {
    const message = data.message ?? data.detail?.message ?? data.detail ?? "请求失败";
    throw new Error(typeof message === "string" ? message : "请求失败");
  }
  return data;
}

export function listArtStyles(): Promise<ArtStyle[]> {
  return jsonRequest("/art-styles");
}

export function createArtStyle(body: Omit<ArtStyle, "id">): Promise<ArtStyle> {
  return jsonRequest("/art-styles", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function listProjects(): Promise<Project[]> {
  return jsonRequest("/projects");
}

export function listEpisodes(projectId: number): Promise<EpisodeSummary[]> {
  return jsonRequest(`/projects/${projectId}/episodes`);
}

export function listCharacters(projectId: number): Promise<CharacterAsset[]> {
  return jsonRequest(`/projects/${projectId}/characters`);
}

export function generateCharacterAsset(body: {
  character_id: number;
  art_style_id?: number;
}): Promise<AssetGenerationResult> {
  return jsonRequest("/assets/character/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export type TimelineTrackName = "video" | "voiceover" | "subtitle" | "music";

export interface TimelineClip {
  storyboard_id: number | null;
  index: number;
  start: number;
  duration: number;
  video_url: string | null;
  audio_url: string | null;
  subtitle_text: string | null;
  subtitle_url: string | null;
  thumbnail: string | null;
}

export interface TimelineTrack {
  enabled: boolean;
  clips: TimelineClip[];
}

export interface TimelineDocument {
  episode_id: number;
  duration: number;
  tracks: Record<TimelineTrackName, TimelineTrack>;
}

export interface TimelineExportResult {
  status: "completed" | "failed" | "blocked";
  merged_url?: string | null;
  duration?: number;
  error?: string | null;
  blocked?: boolean;
  storyboard_id?: number | null;
  clip_index?: number;
}

export function getTimeline(episodeId: number): Promise<TimelineDocument> {
  return jsonRequest(`/timeline/${episodeId}`);
}

export function saveTimeline(episodeId: number, timeline: TimelineDocument): Promise<TimelineDocument> {
  return jsonRequest(`/timeline/${episodeId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tracks: timeline.tracks }),
  });
}

export async function exportTimeline(episodeId: number): Promise<TimelineExportResult> {
  const response = await fetch(`${API}/timeline/${episodeId}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  const data = await response.json();
  if (response.status === 451) return data as TimelineExportResult;
  if (!response.ok) {
    return { status: "failed", merged_url: null, error: data.error ?? data.detail ?? "导出失败" };
  }
  return data as TimelineExportResult;
}
