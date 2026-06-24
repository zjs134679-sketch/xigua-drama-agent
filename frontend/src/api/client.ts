const API = "/api";
const AUTH_API = import.meta.env.VITE_AUTH_URL || "http://127.0.0.1:8100";
const TOKEN_KEY = "xigua.auth.token";
const USER_KEY = "xigua.auth.user";
const AUTH_TIMEOUT_MS = 5000;

export const APP_VERSION = "0.1.0";

export interface AuthUser {
  username: string;
  plan: string;
  role: string;
  violation_count: number;
  banned: boolean;
  banned_reason: string | null;
}

export interface AuthSession {
  token: string;
  user: AuthUser;
}

export interface VersionInfo {
  latest: string;
  url: string;
  notes: string;
}

export class AuthError extends Error {
  status: number;
  banned: boolean;
  reason?: string;

  constructor(message: string, status = 0, banned = false, reason?: string) {
    super(message);
    this.name = "AuthError";
    this.status = status;
    this.banned = banned;
    this.reason = reason;
  }
}

let memoryToken: string | null = null;

function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY) || memoryToken;
  } catch {
    return memoryToken;
  }
}

function saveSession(session: AuthSession): void {
  memoryToken = session.token;
  try {
    localStorage.setItem(TOKEN_KEY, session.token);
    localStorage.setItem(USER_KEY, JSON.stringify(session.user));
  } catch {
    // Memory storage keeps the current desktop session usable.
  }
}

function parseAuthUser(value: unknown): AuthUser | null {
  if (!value || typeof value !== "object") return null;
  const user = value as Record<string, unknown>;
  if (typeof user.username !== "string" || !user.username.trim() || typeof user.banned !== "boolean") {
    return null;
  }
  return {
    username: user.username,
    plan: typeof user.plan === "string" ? user.plan : "free",
    role: typeof user.role === "string" ? user.role : "user",
    violation_count: typeof user.violation_count === "number" ? user.violation_count : 0,
    banned: user.banned,
    banned_reason: typeof user.banned_reason === "string" ? user.banned_reason : null,
  };
}

export function logout(): void {
  memoryToken = null;
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  } catch {
    // Storage can be unavailable in restricted webviews.
  }
}

function authorized(init?: RequestInit): RequestInit {
  const headers = new Headers(init?.headers);
  const token = readToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return { ...init, headers };
}

function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${API}${path}`, authorized(init));
}

async function responseData(response: Response): Promise<Record<string, unknown>> {
  try {
    const data: unknown = await response.json();
    return data && typeof data === "object" ? data as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function authFailure(response: Response, data: Record<string, unknown>): AuthError {
  const detail = data.detail;
  const detailObject = detail && typeof detail === "object" ? detail as Record<string, unknown> : undefined;
  const banned = response.status === 403 || detailObject?.banned === true;
  const reason = typeof detailObject?.reason === "string" ? detailObject.reason : undefined;
  const detailMessage = typeof detailObject?.message === "string" ? detailObject.message : undefined;
  const message = detailMessage || (typeof detail === "string" ? detail : undefined) || "认证服务暂不可用";
  return new AuthError(message, response.status, banned, reason);
}

async function authRequest(path: string, init?: RequestInit): Promise<Record<string, unknown>> {
  let response: Response;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), AUTH_TIMEOUT_MS);
  try {
    response = await fetch(`${AUTH_API}${path}`, authorized({ ...init, signal: controller.signal }));
  } catch {
    throw new AuthError("认证服务暂不可用");
  } finally {
    window.clearTimeout(timeout);
  }
  const data = await responseData(response);
  if (!response.ok) throw authFailure(response, data);
  return data;
}

export async function authenticate(
  mode: "login" | "register",
  username: string,
  password: string,
): Promise<AuthSession> {
  const data = await authRequest(`/auth/${mode}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const token = data.token;
  const user = parseAuthUser(data.user);
  if (typeof token !== "string" || !token || !user) {
    throw new AuthError("认证服务返回了无效数据");
  }
  const session = { token, user };
  saveSession(session);
  return session;
}

export async function restoreAuthSession(): Promise<AuthSession | null> {
  const token = readToken();
  if (!token) return null;
  const user = parseAuthUser(await authRequest("/auth/me"));
  if (!user) throw new AuthError("认证服务返回了无效数据");
  const session = { token, user };
  saveSession(session);
  return session;
}

export async function getLatestVersion(): Promise<VersionInfo> {
  const data = await authRequest("/version");
  return {
    latest: typeof data.latest === "string" ? data.latest : "",
    url: typeof data.url === "string" ? data.url : "",
    notes: typeof data.notes === "string" ? data.notes : "",
  };
}

export function isNewerVersion(latest: string, current = APP_VERSION): boolean {
  const parse = (value: string) => value.trim().replace(/^v/i, "").split(".").map((part) => {
    const match = part.match(/^\d+/);
    return match ? Number(match[0]) : Number.NaN;
  });
  const next = parse(latest);
  const installed = parse(current);
  if (!latest.trim() || next.some(Number.isNaN) || installed.some(Number.isNaN)) return false;
  const length = Math.max(next.length, installed.length);
  for (let index = 0; index < length; index += 1) {
    const difference = (next[index] ?? 0) - (installed[index] ?? 0);
    if (difference !== 0) return difference > 0;
  }
  return false;
}

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
  const r = await apiFetch("/compliance/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  return r.json();
}

export async function getComputeHealth(): Promise<{ type: string; base_url: string; online: boolean }> {
  const r = await apiFetch("/compute/health");
  return r.json();
}

export type ComputeNodeType = "local_comfy" | "remote_comfy" | "cloud_api";

export interface ComputeNodeRecord {
  id: number;
  name: string;
  type: ComputeNodeType;
  base_url: string;
  provider: string | null;
  model: string | null;
  priority: number;
  is_active: boolean;
  capabilities: string | null;
  last_status: "online" | "offline" | null;
  token_configured: boolean;
  api_key_configured: boolean;
  api_key_masked: string | null;
  model_settings: Record<string, unknown>;
  adapter_filename: string | null;
  adapter_configured: boolean;
  adapter_code: string;
}

export interface ComputeNodeInput {
  name: string;
  type: ComputeNodeType;
  base_url: string;
  token?: string;
  provider?: string;
  api_key?: string;
  model?: string;
  priority: number;
  is_active: boolean;
  capabilities?: string;
  model_settings?: Record<string, unknown>;
  adapter_code?: string;
  adapter_filename?: string;
}

export interface ComputeNodeTestResult {
  id: number;
  online: boolean;
  type: ComputeNodeType;
  base_url: string;
  error: string | null;
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
  const r = await apiFetch("/script/draft", {
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

// 落库版：按 episode_id 生成并把剧本写回该分集
export async function generateEpisodeScript(episodeId: number, username = "local"): Promise<ScriptDraftResponse> {
  const r = await apiFetch("/script/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ episode_id: episodeId, username }),
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
  constraint_manual?: string | null;
}

export interface Project {
  id: number;
  title: string;
  description?: string | null;
  genre?: string | null;
  style?: string | null;
  status?: string | null;
  total_episodes?: number | null;
}

export interface EpisodeSummary {
  id: number;
  drama_id: number;
  episode_number: number;
  title: string;
  status: string;
  has_content?: boolean;
  has_script?: boolean;
}

export interface EpisodeDetail extends EpisodeSummary {
  content: string | null;
  script_content: string | null;
}

export interface DramaInput {
  title: string;
  description?: string | null;
  genre?: string | null;
  style?: string | null;
}

export interface EpisodeInput {
  episode_number: number;
  title: string;
  content?: string | null;
}

export interface CharacterAsset {
  id: number;
  name: string;
  role: string | null;
  appearance: string | null;
  image_url: string | null;
  image_prompt: string | null;
  personality?: string | null;
  description?: string | null;
  voice_id?: string | null;
  voice_provider?: string | null;
  view_type?: string;
}

export interface VoiceRecord {
  id: number;
  voice_id: string;
  voice_name: string;
  description: string | null;
  language: string | null;
  provider: string;
}

export function listVoices(): Promise<VoiceRecord[]> {
  return jsonRequest("/voices");
}

export function previewVoice(voiceId: string, text?: string): Promise<{ audio_url: string }> {
  return jsonRequest("/voices/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ voice_id: voiceId, text }),
  });
}

export function updateCharacterVoice(
  characterId: number,
  voiceId: string,
  voiceProvider: string,
): Promise<{ id: number; voice_id: string; voice_provider: string }> {
  return jsonRequest(`/projects/characters/${characterId}/voice`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ voice_id: voiceId, voice_provider: voiceProvider }),
  });
}

export function assignVoices(projectId: number): Promise<{
  drama_id: number;
  assignments: { character_id: number; voice_id: string; voice_name: string; voice_provider: string }[];
}> {
  return jsonRequest(`/projects/${projectId}/assign-voices`, { method: "POST" });
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

export type AssetResolution =
  | "portrait_768x1024"
  | "square_1024x1024"
  | "landscape_1024x576"
  | "hd_portrait_896x1152";

export const ASSET_RESOLUTIONS: { value: AssetResolution; label: string }[] = [
  { value: "portrait_768x1024", label: "竖屏 768×1024" },
  { value: "square_1024x1024", label: "方形 1024×1024" },
  { value: "landscape_1024x576", label: "横屏 1024×576" },
  { value: "hd_portrait_896x1152", label: "高清竖屏 896×1152" },
];

export type AssetTargetType = "character" | "scene" | "prop" | "storyboard";

export interface AssetHistoryItem {
  id: number;
  image_url: string | null;
  local_path: string | null;
  prompt: string | null;
  created_at: string | null;
}

export function listAssetHistory(targetType: AssetTargetType, targetId: number): Promise<AssetHistoryItem[]> {
  return jsonRequest(`/assets/history?target_type=${encodeURIComponent(targetType)}&target_id=${targetId}`);
}

export function useAssetHistory(imageGenerationId: number): Promise<{
  target_type: AssetTargetType;
  target_id: number;
  image_url: string | null;
  local_path: string | null;
}> {
  return jsonRequest(`/assets/history/${imageGenerationId}/use`, { method: "POST" });
}

async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await apiFetch(url, init);
  } catch {
    throw new Error("无法连接后端服务");
  }
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok) {
    const body = data && typeof data === "object" ? data as Record<string, unknown> : {};
    const detail = body.detail;
    const detailObject = detail && typeof detail === "object" ? detail as Record<string, unknown> : {};
    const message = body.message ?? detailObject.message ?? detail ?? "请求失败";
    throw new Error(typeof message === "string" ? message : "请求失败");
  }
  return data as T;
}

export function listComputeNodes(): Promise<ComputeNodeRecord[]> {
  return jsonRequest("/compute/nodes");
}

export function createComputeNode(body: ComputeNodeInput): Promise<ComputeNodeRecord> {
  return jsonRequest("/compute/nodes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function updateComputeNode(
  id: number,
  body: Partial<ComputeNodeInput>,
): Promise<ComputeNodeRecord> {
  return jsonRequest(`/compute/nodes/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function deleteComputeNode(id: number): Promise<{ deleted: boolean }> {
  return jsonRequest(`/compute/nodes/${id}`, { method: "DELETE" });
}

export function testComputeNode(id: number): Promise<ComputeNodeTestResult> {
  return jsonRequest(`/compute/nodes/${id}/test`, { method: "POST" });
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

export function updateArtStyle(id: number, body: Partial<Omit<ArtStyle, "id">>): Promise<ArtStyle> {
  return jsonRequest(`/art-styles/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function listProjects(): Promise<Project[]> {
  return jsonRequest("/projects");
}

export function createDrama(body: DramaInput): Promise<Project> {
  return jsonRequest("/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function deleteDrama(projectId: number): Promise<{
  deleted: boolean;
  project_id: number;
  files_deleted: number;
  files_skipped: number;
  file_errors: string[];
}> {
  return jsonRequest(`/projects/${projectId}`, { method: "DELETE" });
}

export function listEpisodes(projectId: number): Promise<EpisodeSummary[]> {
  return jsonRequest(`/projects/${projectId}/episodes`);
}

export function createEpisode(projectId: number, body: EpisodeInput): Promise<EpisodeDetail> {
  return jsonRequest(`/projects/${projectId}/episodes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export interface NovelImportResult {
  status: number;
  created?: number;
  episodes?: EpisodeSummary[];
  warn?: boolean;
  level?: string;
  message?: string;
  hits?: ComplianceHit[];
  banned?: boolean;
}

export async function importNovel(projectId: number, text: string, username = "local"): Promise<NovelImportResult> {
  const r = await apiFetch(`/projects/${projectId}/import-novel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, username }),
  });
  const data = await r.json();
  if (r.ok) return { status: r.status, created: data.created, episodes: data.episodes, warn: data.warn };
  const detail = data.detail;
  if (detail && typeof detail === "object") {
    return { status: r.status, level: detail.level, message: detail.message, hits: detail.hits, banned: detail.banned };
  }
  return { status: r.status, message: typeof detail === "string" ? detail : "导入失败" };
}

export function getEpisode(episodeId: number): Promise<EpisodeDetail> {
  return jsonRequest(`/projects/episodes/${episodeId}`);
}

export function listCharacters(projectId: number): Promise<CharacterAsset[]> {
  return jsonRequest(`/projects/${projectId}/characters`);
}

export function generateCharacterAsset(body: {
  character_id: number;
  prompt?: string;
  art_style_id?: number;
  username?: string;
  node_id?: number;
  resolution?: AssetResolution;
  extra?: string;
  view_type?: string;
}): Promise<AssetGenerationResult> {
  return jsonRequest("/assets/character/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function updateCharacterPrompt(characterId: number, prompt: string): Promise<{ id: number; image_prompt: string }> {
  return jsonRequest(`/projects/characters/${characterId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
}

export function updateScenePrompt(sceneId: number, prompt: string): Promise<{ id: number; prompt: string }> {
  return jsonRequest(`/projects/scenes/${sceneId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
}

export interface SceneAsset {
  id: number;
  location: string | null;
  time: string | null;
  prompt: string | null;
  status: string | null;
  image_url: string | null;
}

export function listScenes(projectId: number): Promise<SceneAsset[]> {
  return jsonRequest(`/projects/${projectId}/scenes`);
}

export function generateSceneAsset(body: {
  scene_id: number;
  prompt?: string;
  art_style_id?: number;
  username?: string;
  node_id?: number;
  resolution?: AssetResolution;
  extra?: string;
}): Promise<AssetGenerationResult> {
  return jsonRequest("/assets/scene/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export interface PropAsset {
  id: number;
  name: string;
  type: string | null;
  description: string | null;
  prompt: string | null;
  image_url: string | null;
}

export function listProps(projectId: number): Promise<PropAsset[]> {
  return jsonRequest(`/projects/${projectId}/props`);
}

export function generatePropAsset(body: {
  prop_id: number;
  prompt?: string;
  art_style_id?: number;
  username?: string;
  node_id?: number;
  resolution?: AssetResolution;
  extra?: string;
}): Promise<AssetGenerationResult> {
  return jsonRequest("/assets/prop/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function updatePropPrompt(propId: number, prompt: string): Promise<{ id: number; prompt: string }> {
  return jsonRequest(`/projects/props/${propId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
}

export interface ExtractResult {
  status: number;
  new?: { characters: number; scenes: number; props: number };
  warn?: boolean;
  level?: string;
  message?: string;
  hits?: ComplianceHit[];
  banned?: boolean;
}

// 从分集剧本/原文 AI 提取角色 + 场景 + 道具
export async function extractFromEpisode(episodeId: number, username = "local"): Promise<ExtractResult> {
  const r = await apiFetch("/extract", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ episode_id: episodeId, username }),
  });
  const data = await r.json();
  if (r.ok) return { status: r.status, new: data.new, warn: data.warn };
  const detail = data.detail;
  if (detail && typeof detail === "object") {
    return { status: r.status, level: detail.level, message: detail.message, hits: detail.hits, banned: detail.banned };
  }
  return { status: r.status, message: typeof detail === "string" ? detail : "提取失败" };
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
  const response = await apiFetch(`/timeline/${episodeId}/export`, {
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

export function generateStoryboardTTS(
  storyboardId: number,
  username?: string,
): Promise<{ storyboard_id: number; status: string; audio_url: string }> {
  return jsonRequest(`/timeline/storyboards/${storyboardId}/tts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username }),
  });
}

// ===== 分镜 =====
export interface Storyboard {
  id: number;
  episode_id: number;
  storyboard_number: number;
  title: string | null;
  location: string | null;
  time: string | null;
  shot_type: string | null;
  angle: string | null;
  movement: string | null;
  action: string | null;
  result: string | null;
  dialogue: string | null;
  atmosphere: string | null;
  image_prompt: string | null;
  video_prompt: string | null;
  bgm_prompt: string | null;
  sound_effect: string | null;
  description: string | null;
  duration: number;
  speaking_character_id: number | null;
  image_url: string | null;
  status: string;
  reference_mode: "auto" | "manual";
  reference_images: StoryboardReference[];
}

export interface StoryboardReference {
  url: string;
  preview_url: string;
  kind: "scene" | "continuity" | "previous" | "character" | "manual";
  label: string;
  source_storyboard_id: number | null;
}

export interface StoryboardGenResult {
  status: number;
  count?: number;
  storyboards?: Storyboard[];
  warn?: boolean;
  level?: string;
  message?: string;
  hits?: ComplianceHit[];
  banned?: boolean;
}

export interface StoryboardImageResult {
  status: number;
  image_url?: string | null;
  warn?: boolean;
  level?: string;
  message?: string;
  hits?: ComplianceHit[];
  banned?: boolean;
}

export function listStoryboards(episodeId: number): Promise<Storyboard[]> {
  return jsonRequest(`/storyboard?episode_id=${episodeId}`);
}

export type ReviewSeverity = "severe" | "medium" | "minor";

export interface StoryboardReviewIssue {
  id: number;
  severity: ReviewSeverity;
  category: string;
  storyboard_numbers: number[];
  problem: string;
  suggestion: string;
}

export interface StoryboardReviewReport {
  id: number;
  episode_id: number;
  grade: "A" | "B" | "C" | "D";
  summary: string;
  counts: Record<ReviewSeverity, number>;
  issues: StoryboardReviewIssue[];
  decisions: string[];
  model: string | null;
  instruction: string | null;
  created_at: string | null;
  remediation?: {
    applied_at: string;
    model: string;
    instruction: string | null;
    summary: string;
    changes: StoryboardRemediationChange[];
  };
}

export interface StoryboardRemediationChange {
  storyboard_id: number;
  storyboard_number: number;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
}

export function getLatestStoryboardReview(episodeId: number): Promise<StoryboardReviewReport | null> {
  return jsonRequest(`/storyboard-review/latest?episode_id=${episodeId}`);
}

export function listStoryboardReviews(episodeId: number): Promise<StoryboardReviewReport[]> {
  return jsonRequest(`/storyboard-review?episode_id=${episodeId}`);
}

export function runStoryboardReview(body: {
  episode_id: number;
  username?: string;
  instruction?: string;
}): Promise<StoryboardReviewReport> {
  return jsonRequest("/storyboard-review", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function remediateStoryboardReview(
  reviewId: number,
  body: { username?: string; instruction?: string },
): Promise<{ review: StoryboardReviewReport; changed_count: number; changes: StoryboardRemediationChange[] }> {
  return jsonRequest(`/storyboard-review/${reviewId}/remediate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function generateStoryboards(episodeId: number, username = "local"): Promise<StoryboardGenResult> {
  const r = await apiFetch("/storyboard/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ episode_id: episodeId, username }),
  });
  const data = await r.json();
  if (r.ok) return { status: r.status, count: data.count, storyboards: data.storyboards, warn: data.warn };
  const detail = data.detail;
  if (detail && typeof detail === "object") {
    return { status: r.status, level: detail.level, message: detail.message, hits: detail.hits, banned: detail.banned };
  }
  return { status: r.status, message: typeof detail === "string" ? detail : "分镜生成失败" };
}

export type StoryboardUpdate = Partial<Pick<Storyboard,
  "title" | "location" | "time" | "shot_type" | "angle" | "movement" | "action" | "result" |
  "atmosphere" | "image_prompt" | "video_prompt" | "bgm_prompt" | "sound_effect" | "dialogue" |
  "description" | "duration" | "speaking_character_id"
>> & { reference_images?: string[] | null };

export function updateStoryboard(storyboardId: number, changes: StoryboardUpdate): Promise<Storyboard> {
  return jsonRequest(`/storyboard/${storyboardId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
}

export function updateStoryboardPrompt(storyboardId: number, imagePrompt: string): Promise<Storyboard> {
  return updateStoryboard(storyboardId, { image_prompt: imagePrompt });
}

export interface BatchStoryboardImageResult {
  storyboard_id: number;
  status: "completed" | "blocked" | "failed";
  image_url?: string;
  error?: string;
  hits?: ComplianceHit[];
}

export function batchDeleteStoryboards(ids: number[]): Promise<{ deleted: number }> {
  return jsonRequest("/storyboard/batch-delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  });
}

export function batchGenerateStoryboardImages(body: {
  ids: number[];
  art_style_id?: number;
  username?: string;
  node_id?: number;
  resolution?: string;
}): Promise<{ results: BatchStoryboardImageResult[] }> {
  return jsonRequest("/storyboard/batch-generate-images", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function batchGenerateStoryboardPrompts(body: {
  ids: number[];
  temperature?: number;
  username?: string;
}): Promise<{ updated: number; prompts: { number: number; prompt: string }[] }> {
  return jsonRequest("/storyboard/batch-generate-prompts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ===== 视频生成 =====
export interface VideoGenResult {
  video_id: number;
  storyboard_id: number;
  status: string;
  video_url: string | null;
  prompt: string | null;
  error_msg?: string | null;
  duration?: number | null;
}

export interface VideoGenInput {
  storyboard_id: number;
  prompt?: string | null;
  model?: string;
  reference_mode?: string;
  node_id?: number;
  username?: string;
  duration?: number;
  resolution?: string;
  extra?: string;
}

export interface BatchVideoGenInput {
  storyboard_ids: number[];
  model?: string;
  reference_mode?: string;
  node_id?: number;
  username?: string;
  duration?: number;
  resolution?: string;
}

export interface VideoPromptInput {
  storyboard_id: number;
  model?: string;
  mode?: string;
}

export function generateVideoPrompt(body: VideoPromptInput): Promise<{ storyboard_id: number; model: string; mode: string; prompt: string }> {
  return jsonRequest("/video/generate-prompt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function generateVideo(body: VideoGenInput): Promise<VideoGenResult> {
  return jsonRequest("/video/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function batchGenerateVideo(body: BatchVideoGenInput): Promise<{ results: { storyboard_id: number; status: string; video_url?: string | null; video_id?: number; error?: string; hits?: ComplianceHit[] }[] }> {
  return jsonRequest("/video/batch-generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function checkVideoStatus(videoId: number): Promise<VideoGenResult> {
  return jsonRequest(`/video/poll/${videoId}`);
}

export function batchCheckVideoStatus(ids: number[]): Promise<{ results: VideoGenResult[] }> {
  return jsonRequest("/video/poll-batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  });
}

export function listStoryboardVideos(storyboardId: number): Promise<VideoGenResult[]> {
  return jsonRequest(`/video/list/${storyboardId}`);
}

export function deleteVideo(videoId: number): Promise<{ deleted: boolean; video_id?: number }> {
  return jsonRequest(`/video/${videoId}`, { method: "DELETE" });
}

export async function generateStoryboardImage(
  storyboardId: number,
  username = "local",
  artStyleId?: number,
  prompt?: string,
  options?: { node_id?: number; resolution?: AssetResolution; extra?: string },
): Promise<StoryboardImageResult> {
  const r = await apiFetch(`/storyboard/${storyboardId}/image`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username,
      art_style_id: artStyleId ?? null,
      prompt: prompt ?? null,
      node_id: options?.node_id ?? null,
      resolution: options?.resolution ?? null,
      extra: options?.extra ?? null,
    }),
  });
  const data = await r.json();
  if (r.ok) return { status: r.status, image_url: data.image_url, warn: data.warn, hits: data.warn_hits };
  if (r.status === 451) {
    return { status: r.status, level: "red", message: data.message, hits: data.hits, banned: data.banned };
  }
  return { status: r.status, message: data.detail ?? data.error ?? "出图失败" };
}

// ===== 设置：LLM（编剧 / 分镜 Agent）=====
export interface LLMConfig {
  configured: boolean;
  source: "db" | "env" | "none";
  provider: string | null;
  base_url: string | null;
  model: string | null;
  api_key_configured: boolean;
  api_key_masked: string | null;
}

export interface LLMConfigInput {
  provider?: string | null;
  base_url: string;
  model?: string | null;
  api_key?: string | null; // 留空 = 沿用已存 key
}

export interface LLMTestResult {
  ok: boolean;
  message: string;
  latency_ms?: number | null;
  reply?: string | null;
}

export function getLLMConfig(): Promise<LLMConfig> {
  return jsonRequest("/settings/llm");
}

export function saveLLMConfig(body: LLMConfigInput): Promise<LLMConfig> {
  return jsonRequest("/settings/llm", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function testLLMConfig(body: Partial<LLMConfigInput>): Promise<LLMTestResult> {
  return jsonRequest("/settings/llm/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ===== Agent 流式对话 =====
export interface AgentChunk {
  type: "phase" | "text" | "tool_call" | "tool_result" | "done" | "error";
  phase?: string;
  content?: string;
  message?: string;
  function?: { name: string; arguments: string };
  name?: string;
  result?: string;
  messages?: Record<string, unknown>[];
}

export type AgentStreamCallback = (chunk: AgentChunk) => void;

export function agentChatStream(
  body: { skill_name?: string; system_prompt?: string; message: string; temperature?: number; context?: Record<string, unknown>[] },
  onChunk: AgentStreamCallback,
  signal?: AbortSignal,
): Promise<void> {
  return agentSSE("/agent/chat/stream", body, onChunk, signal);
}

export function agentQuick(body: {
  skill_name: string;
  message: string;
  temperature?: number;
  instruction?: string;
}): Promise<{ content: string }> {
  return jsonRequest("/agent/quick", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function agentSSE(
  path: string,
  body: Record<string, unknown>,
  onChunk: AgentStreamCallback,
  signal?: AbortSignal,
): Promise<void> {
  const token = readToken();
  const response = await fetch(`${API}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(text || `HTTP ${response.status}`);
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("不支持流式响应");
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          const chunk = JSON.parse(line.slice(6)) as AgentChunk;
          onChunk(chunk);
        } catch { /* skip malformed */ }
      }
    }
  }
}

// ===== 技能库 =====
export interface SkillMeta {
  name: string;
  display_name: string;
  description: string;
  category: string;
  tags: string[];
  path: string;
}

export interface SkillDetail extends SkillMeta {
  content: string;
}

export function listStoryTypes(): Promise<SkillMeta[]> {
  return jsonRequest("/skills/story-types");
}

export function listAgentSkills(): Promise<SkillMeta[]> {
  return jsonRequest("/skills/agents");
}

export function listAllSkills(category?: string): Promise<SkillMeta[]> {
  const qs = category ? `?category=${encodeURIComponent(category)}` : "";
  return jsonRequest(`/skills${qs}`);
}

export function getSkillDetail(category: string, name: string): Promise<SkillDetail> {
  return jsonRequest(`/skills/${encodeURIComponent(category)}/${encodeURIComponent(name)}`);
}

export function searchSkills(query: string): Promise<(SkillMeta & { _score: number })[]> {
  return jsonRequest(`/skills/search?q=${encodeURIComponent(query)}`);
}

// ===== 提示词润色 =====
export interface PolishResult {
  original: string;
  polished: string;
  asset_type: string;
}

export interface BatchPolishResult {
  results: { id: number | string; polished: string }[];
}

export function polishPrompt(body: {
  asset_type?: string;
  prompt: string;
  context?: string;
  temperature?: number;
}): Promise<PolishResult> {
  return jsonRequest("/assets/polish-prompt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function batchPolishPrompts(body: {
  asset_type?: string;
  items: { id: number | string; prompt: string; context?: string }[];
  temperature?: number;
}): Promise<BatchPolishResult> {
  return jsonRequest("/assets/polish-prompt/batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
