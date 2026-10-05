const API = "/api";
/** 构建时默认；运行时由后端 /setup/public-config 覆盖（卖客户时改安装目录 config.env） */
const AUTH_API_DEFAULT = import.meta.env.VITE_AUTH_URL || "http://127.0.0.1:8100";
const TOKEN_KEY = "xigua.auth.token";
const USER_KEY = "xigua.auth.user";
const CAP_KEY = "xigua.auth.capability";
const MACHINE_KEY = "xigua.machine_id";
const AUTH_TIMEOUT_MS = 5000;

let authApiBase = AUTH_API_DEFAULT;
let authConfigLoaded = false;

/** 从短剧后端读取授权服地址（与 XIGUA_AUTH_SERVER_URL 一致）。 */
export async function loadAuthServerConfig(): Promise<string> {
  if (authConfigLoaded) return authApiBase;
  try {
    const res = await fetch(`${API}/setup/public-config`, { signal: AbortSignal.timeout(4000) });
    if (res.ok) {
      const data = (await res.json()) as { auth_server_url?: string };
      if (typeof data.auth_server_url === "string" && data.auth_server_url.trim()) {
        authApiBase = data.auth_server_url.trim().replace(/\/$/, "");
      }
    }
  } catch {
    // 后端未起时保留默认（开发本机 8100）
  }
  authConfigLoaded = true;
  return authApiBase;
}

export function getAuthServerUrl(): string {
  return authApiBase;
}

export const APP_VERSION = "0.1.0";

export interface AuthUser {
  username: string;
  plan: string;
  role: string;
  violation_count: number;
  banned: boolean;
  banned_reason: string | null;
  expire_at: string | null;
  license_active: boolean;
  license_required: boolean;
  license_reason: string | null;
  machines_bound: number;
  max_machines: number;
}

/**
 * 本机设备码（持久化）。
 * 格式 xg2-<uuid>：uuid 防冲突；前缀标识加固版本。
 * 注意：Web 环境无法做真正硬件 TPM 绑定，清缓存会变新机。
 */
export function getMachineId(): string {
  try {
    let id = localStorage.getItem(MACHINE_KEY);
    if (!id) {
      const uuid =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `m-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
      id = `xg2-${uuid}`;
      localStorage.setItem(MACHINE_KEY, id);
      try {
        const envHint = [
          navigator.userAgent,
          navigator.language,
          `${screen.width}x${screen.height}@${screen.colorDepth}`,
          Intl.DateTimeFormat().resolvedOptions().timeZone || "",
        ].join("|");
        localStorage.setItem(`${MACHINE_KEY}.env`, envHint.slice(0, 400));
      } catch {
        /* ignore */
      }
    }
    return id;
  } catch {
    return "anonymous-device";
  }
}

export interface AuthSession {
  token: string;
  user: AuthUser;
  capability?: string | null;
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
let memoryCapability: string | null = null;

function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY) || memoryToken;
  } catch {
    return memoryToken;
  }
}

function readCapability(): string | null {
  try {
    return localStorage.getItem(CAP_KEY) || memoryCapability;
  } catch {
    return memoryCapability;
  }
}

function rememberCapabilityFrom(data: Record<string, unknown>, userObj?: Record<string, unknown> | null): void {
  const fromRoot = typeof data.capability === "string" ? data.capability : null;
  const fromUser =
    userObj && typeof userObj.capability === "string" ? (userObj.capability as string) : null;
  const cap = fromRoot || fromUser;
  if (!cap) return;
  memoryCapability = cap;
  try {
    localStorage.setItem(CAP_KEY, cap);
  } catch {
    /* ignore */
  }
}

function saveSession(session: AuthSession): void {
  memoryToken = session.token;
  if (session.capability) {
    memoryCapability = session.capability;
  }
  try {
    localStorage.setItem(TOKEN_KEY, session.token);
    localStorage.setItem(USER_KEY, JSON.stringify(session.user));
    if (session.capability) {
      localStorage.setItem(CAP_KEY, session.capability);
    }
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
  const licenseActive =
    typeof user.license_active === "boolean"
      ? user.license_active
      : !user.banned; // 旧版 auth-server 兼容：无字段时仅看封禁
  return {
    username: user.username,
    plan: typeof user.plan === "string" ? user.plan : "free",
    role: typeof user.role === "string" ? user.role : "user",
    violation_count: typeof user.violation_count === "number" ? user.violation_count : 0,
    banned: user.banned,
    banned_reason: typeof user.banned_reason === "string" ? user.banned_reason : null,
    expire_at: typeof user.expire_at === "string" ? user.expire_at : null,
    license_active: licenseActive,
    license_required: typeof user.license_required === "boolean" ? user.license_required : true,
    license_reason: typeof user.license_reason === "string" ? user.license_reason : null,
    machines_bound: typeof user.machines_bound === "number" ? user.machines_bound : 0,
    max_machines: typeof user.max_machines === "number" ? user.max_machines : 2,
  };
}

export function logout(): void {
  memoryToken = null;
  memoryCapability = null;
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(CAP_KEY);
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
  if (!headers.has("X-Machine-Id")) {
    headers.set("X-Machine-Id", getMachineId());
  }
  const cap = readCapability();
  if (cap && !headers.has("X-Capability")) {
    headers.set("X-Capability", cap);
  }
  // FormData 必须由浏览器自动带 multipart boundary；禁止手写 Content-Type
  if (init?.body instanceof FormData) {
    headers.delete("Content-Type");
  }
  return { ...init, headers };
}

function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${API}${path}`, authorized(init));
}

/** 把后端 /oss、裸文件名转成浏览器可访问的地址（开发走 /api 反代） */
export function mediaDisplayUrl(url: string | null | undefined, bustCache = false): string {
  if (!url || !String(url).trim()) return "";
  let text = String(url).trim();
  if (text.startsWith("blob:") || text.startsWith("data:") || text.startsWith("http://") || text.startsWith("https://")) {
    return text;
  }
  // 本地绝对路径 → 取文件名
  if (/^[A-Za-z]:[\\/]/.test(text) || text.startsWith("\\\\")) {
    text = text.replace(/\\/g, "/").split("/").pop() || text;
  }
  if (text.startsWith("/api/oss/")) {
    /* already ok */
  } else if (text.startsWith("/oss/")) {
    text = `/api${text}`;
  } else if (!text.startsWith("/")) {
    text = `/api/oss/${text.replace(/^\/+/, "")}`;
  }
  if (bustCache) {
    const sep = text.includes("?") ? "&" : "?";
    text = `${text}${sep}t=${Date.now()}`;
  }
  return text;
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
  const banned = detailObject?.banned === true;
  const reason =
    typeof detailObject?.reason === "string"
      ? detailObject.reason
      : typeof detailObject?.message === "string"
        ? detailObject.message
        : undefined;
  const detailMessage = typeof detailObject?.message === "string" ? detailObject.message : undefined;
  const message = detailMessage || (typeof detail === "string" ? detail : undefined) || "认证服务暂不可用";
  return new AuthError(message, response.status, banned, reason);
}

async function authRequest(path: string, init?: RequestInit): Promise<Record<string, unknown>> {
  await loadAuthServerConfig();
  let response: Response;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), AUTH_TIMEOUT_MS);
  try {
    response = await fetch(`${authApiBase}${path}`, authorized({ ...init, signal: controller.signal }));
  } catch {
    throw new AuthError(`认证服务暂不可用（${authApiBase}）。请确认 auth-server 已启动，或检查 config.env 中 XIGUA_AUTH_SERVER_URL`);
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
    body: JSON.stringify({ username, password, machine_id: getMachineId() }),
  });
  const token = data.token;
  const user = parseAuthUser(data.user);
  if (typeof token !== "string" || !token || !user) {
    throw new AuthError("认证服务返回了无效数据");
  }
  rememberCapabilityFrom(data, data.user as Record<string, unknown>);
  const session: AuthSession = {
    token,
    user,
    capability: typeof data.capability === "string" ? data.capability : readCapability(),
  };
  saveSession(session);
  return session;
}

/** 用户自助改密（需旧密码）。与密码管理平台共用 auth.db。 */
export async function changePassword(
  username: string,
  oldPassword: string,
  newPassword: string,
): Promise<{ ok: boolean; message: string }> {
  const data = await authRequest("/auth/change-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: username.trim(),
      old_password: oldPassword,
      new_password: newPassword,
    }),
  });
  return {
    ok: data.ok === true,
    message: typeof data.message === "string" ? data.message : "密码已修改",
  };
}

export async function restoreAuthSession(): Promise<AuthSession | null> {
  const token = readToken();
  if (!token) return null;
  const data = await authRequest("/auth/me");
  const user = parseAuthUser(data);
  if (!user) throw new AuthError("认证服务返回了无效数据");
  rememberCapabilityFrom(data, data as Record<string, unknown>);
  const session: AuthSession = {
    token,
    user,
    capability: typeof data.capability === "string" ? data.capability : readCapability(),
  };
  saveSession(session);
  return session;
}

export async function activateLicense(code: string): Promise<AuthSession> {
  const data = await authRequest("/license/activate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code: code.trim(), machine_id: getMachineId() }),
  });
  const user = parseAuthUser(data.user);
  const token =
    typeof data.token === "string" && data.token ? data.token : readToken();
  if (!user || !token) throw new AuthError("激活失败：返回数据无效");
  rememberCapabilityFrom(data, data.user as Record<string, unknown>);
  const session: AuthSession = {
    token,
    user,
    capability: typeof data.capability === "string" ? data.capability : readCapability(),
  };
  saveSession(session);
  return session;
}

export async function licenseHeartbeat(): Promise<AuthUser> {
  const data = await authRequest("/auth/heartbeat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ machine_id: getMachineId() }),
  });
  const user = parseAuthUser(data);
  if (!user) throw new AuthError("心跳校验失败");
  rememberCapabilityFrom(data, data as Record<string, unknown>);
  const token = readToken();
  if (token) {
    saveSession({
      token,
      user,
      capability: typeof data.capability === "string" ? data.capability : readCapability(),
    });
  }
  return user;
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

export async function getComputeHealth(): Promise<{
  type: string;
  base_url: string;
  online: boolean;
  image?: { type: string; online: boolean; provider?: string | null };
  video?: { type: string; online: boolean; provider?: string | null };
  readiness?: {
    mode?: string;
    cloud_ready?: boolean;
    has_image_path?: boolean;
    has_video_path?: boolean;
    hint?: string;
  };
}> {
  const r = await apiFetch("/compute/health");
  return r.json();
}

export function getCloudStatus(): Promise<{
  readiness: {
    mode?: string;
    cloud_ready?: boolean;
    hint?: string;
  };
  wan_configured: boolean;
  seedance_configured: boolean;
  wan_model?: string | null;
  seedance_model?: string | null;
}> {
  return jsonRequest("/compute/cloud-status");
}

export function cloudQuickSetup(body: {
  wan_api_key?: string;
  seedance_api_key?: string;
  wan_model?: string;
  seedance_model?: string;
  deactivate_comfy?: boolean;
  test_connection?: boolean;
}): Promise<{
  ok: boolean;
  message: string;
  nodes: { id: number; name: string; provider: string; role: string }[];
  tests: { id: number; name: string; provider: string; ok: boolean; error?: string | null }[];
  readiness: { cloud_ready?: boolean; hint?: string };
}> {
  return jsonRequest("/compute/cloud-quick-setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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
  skill_key?: string | null;
  pack_files?: string[];
  pack_complete?: boolean;
}

export interface StyleBible {
  version?: number;
  visual_pack?: string | null;
  visual_name?: string | null;
  narrative_tag?: string | null;
  pacing_profile?: string | null;
  aspect?: string | null;
  art_style_id?: number | null;
  compat_level?: string | null;
  compat_message?: string | null;
}

export interface Project {
  id: number;
  title: string;
  description?: string | null;
  genre?: string | null;
  style?: string | null;
  status?: string | null;
  total_episodes?: number | null;
  style_bible?: StyleBible | null;
  has_director_manual?: boolean;
  has_visual_manual?: boolean;
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
  art_style_id?: number | null;
  pacing_profile?: string | null;
  narrative_tag?: string | null;
  aspect?: string | null;
}

export interface StyleOptions {
  pacing_profiles: { key: string; display_name: string; description: string }[];
  story_types: { key: string; display_name: string; description: string }[];
  aspect_options: string[];
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

export function assignVoices(
  projectId: number,
  force = false,
): Promise<{
  drama_id: number;
  assignments: { character_id: number; character_name?: string; voice_id: string; voice_name: string; voice_provider: string }[];
  count?: number;
  message?: string;
}> {
  const q = force ? "?force=true" : "";
  return jsonRequest(`/projects/${projectId}/assign-voices${q}`, { method: "POST" });
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
  | "fast_portrait_640x896"
  | "fast_landscape_864x480"
  | "portrait_768x1024"
  | "square_1024x1024"
  | "landscape_1024x576"
  | "uhd_landscape_1280x720"
  | "uhd_landscape_1536x864"
  | "hd_portrait_896x1152"
  | "uhd_portrait_1024x1344"
  | "uhd_portrait_1152x1536";

export const ASSET_RESOLUTIONS: { value: AssetResolution; label: string }[] = [
  { value: "uhd_portrait_1024x1344", label: "超清竖屏 1024×1344（角色默认·清晰）" },
  { value: "uhd_portrait_1152x1536", label: "更高竖屏 1152×1536（最清晰·更慢更吃显存）" },
  { value: "hd_portrait_896x1152", label: "高清竖屏 896×1152" },
  { value: "portrait_768x1024", label: "竖屏 768×1024" },
  { value: "fast_portrait_640x896", label: "快速竖屏 640×896（仅预览·易糊）" },
  { value: "uhd_landscape_1280x720", label: "超清横屏 1280×720（场景默认·清晰）" },
  { value: "uhd_landscape_1536x864", label: "更高横屏 1536×864（场景最清晰·更慢）" },
  { value: "landscape_1024x576", label: "横屏 1024×576" },
  { value: "fast_landscape_864x480", label: "快速横屏 864×480（场景预览·易糊）" },
  { value: "square_1024x1024", label: "方形 1024×1024" },
];

/** 场景底板优先横屏/超清横屏 */
export const SCENE_ASSET_RESOLUTIONS: { value: AssetResolution; label: string }[] = [
  { value: "uhd_landscape_1280x720", label: "超清横屏 1280×720（默认·清晰）" },
  { value: "uhd_landscape_1536x864", label: "更高横屏 1536×864（最清晰·更慢）" },
  { value: "landscape_1024x576", label: "横屏 1024×576" },
  { value: "fast_landscape_864x480", label: "快速横屏 864×480（预览）" },
  { value: "square_1024x1024", label: "方形 1024×1024" },
];

/** 角色定装：竖屏（头像/全身）+ 横屏（三视图设定拼图更合适） */
export const CHARACTER_ASSET_RESOLUTIONS: { value: AssetResolution; label: string }[] = [
  { value: "uhd_portrait_1024x1344", label: "超清竖屏 1024×1344（头像/全身默认）" },
  { value: "uhd_portrait_1152x1536", label: "更高竖屏 1152×1536（脸部最清晰·更慢）" },
  { value: "hd_portrait_896x1152", label: "高清竖屏 896×1152" },
  { value: "portrait_768x1024", label: "竖屏 768×1024" },
  { value: "fast_portrait_640x896", label: "快速竖屏 640×896（预览）" },
  { value: "uhd_landscape_1280x720", label: "超清横屏 1280×720（三视图推荐）" },
  { value: "uhd_landscape_1536x864", label: "更高横屏 1536×864（三视图最清晰·更慢）" },
  { value: "landscape_1024x576", label: "横屏 1024×576" },
  { value: "fast_landscape_864x480", label: "快速横屏 864×480（预览）" },
  { value: "square_1024x1024", label: "方形 1024×1024" },
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

/** 导出角色出图 + 多参考视频 调试工作流到 ComfyUI workflows 目录 */
export function exportComfyWorkflows(
  nodeId: number,
  body?: { comfy_user_dir?: string },
): Promise<{
  ok: boolean;
  comfy_workflows_dir?: string | null;
  local_dir?: string;
  files?: string[];
  comfy_files?: string[];
  params_exported?: Record<string, unknown>;
  how_to_open?: string;
}> {
  return jsonRequest(`/compute/nodes/${nodeId}/export-comfy-workflows`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

/** 从 Comfy 最近任务同步 width/height/steps 等到算力节点 */
export function syncComfyParams(
  nodeId: number,
  body?: { source?: "history" | "file"; file_path?: string; apply?: boolean },
): Promise<{
  ok: boolean;
  applied?: boolean;
  message?: string;
  params?: Record<string, unknown>;
  model_settings?: Record<string, unknown>;
  node?: ComputeNodeRecord;
  source_info?: string;
}> {
  return jsonRequest(`/compute/nodes/${nodeId}/sync-comfy-params`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || { source: "history", apply: true }),
  });
}

export function listArtStyles(): Promise<ArtStyle[]> {
  return jsonRequest("/art-styles");
}

export interface ArtStyleSeedResult {
  inserted: number;
  updated: number;
  total_presets: number;
  styles: ArtStyle[];
}

export function seedArtStyles(force = false): Promise<ArtStyleSeedResult> {
  const qs = force ? "?force=true" : "";
  return jsonRequest(`/art-styles/seed${qs}`, { method: "POST" });
}

export function getArtStylePack(id: number): Promise<Record<string, unknown>> {
  return jsonRequest(`/art-styles/${id}/pack`);
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

export function deleteArtStyle(id: number): Promise<{ deleted: boolean }> {
  return jsonRequest(`/art-styles/${id}`, { method: "DELETE" });
}

export function listProjects(): Promise<Project[]> {
  return jsonRequest("/projects");
}

export function getProject(projectId: number): Promise<Project> {
  return jsonRequest(`/projects/${projectId}`);
}

export function createDrama(body: DramaInput): Promise<Project> {
  return jsonRequest("/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function fetchStyleOptions(): Promise<StyleOptions> {
  return jsonRequest("/projects/style-options");
}

export function updateStyleBible(
  projectId: number,
  body: Partial<StyleBible> & { art_style_id?: number | null },
): Promise<Project> {
  return jsonRequest(`/projects/${projectId}/style-bible`, {
    method: "PUT",
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

export function listCharacters(projectId: number, episodeId?: number | null): Promise<CharacterAsset[]> {
  const q =
    episodeId != null && Number.isFinite(episodeId)
      ? `?episode_id=${encodeURIComponent(String(episodeId))}`
      : "";
  return jsonRequest(`/projects/${projectId}/characters${q}`);
}

export function generateCharacterAsset(body: {
  character_id: number;
  prompt?: string;
  art_style_id?: number;
  username?: string;
  node_id?: number;
  resolution?: AssetResolution;
  /** 采样步数 12–50，仅影响角色定妆图，与视频无关 */
  steps?: number;
  extra?: string;
  view_type?: string;
}): Promise<AssetGenerationResult> {
  return jsonRequest("/assets/character/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export type MediaUploadTarget =
  | "character"
  | "scene"
  | "prop"
  | "storyboard_image"
  | "storyboard_video";

export async function uploadMedia(body: {
  file: File;
  target_type: MediaUploadTarget;
  target_id: number;
  username?: string;
}): Promise<{
  target_type: string;
  target_id: number;
  kind: string;
  url: string;
  image_url?: string | null;
  video_url?: string | null;
  composed_image?: string | null;
  first_frame_image?: string | null;
  local_path?: string | null;
}> {
  const form = new FormData();
  form.append("file", body.file);
  form.append("target_type", body.target_type);
  form.append("target_id", String(body.target_id));
  if (body.username) form.append("username", body.username);
  const response = await apiFetch("/assets/upload", { method: "POST", body: form });
  let data: Record<string, unknown> = {};
  try {
    data = (await response.json()) as Record<string, unknown>;
  } catch {
    /* ignore */
  }
  if (!response.ok) {
    const detail = data.detail;
    const msg =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && typeof (detail as { message?: string }).message === "string"
          ? (detail as { message: string }).message
          : typeof data.message === "string"
            ? data.message
            : `上传失败 (${response.status})`;
    throw new Error(msg);
  }
  const url = typeof data.url === "string" ? data.url : "";
  const image_url =
    typeof data.image_url === "string"
      ? data.image_url
      : typeof data.composed_image === "string"
        ? data.composed_image
        : url;
  const video_url = typeof data.video_url === "string" ? data.video_url : null;
  return {
    target_type: String(data.target_type || body.target_type),
    target_id: Number(data.target_id ?? body.target_id),
    kind: String(data.kind || ""),
    url,
    image_url,
    video_url,
    composed_image: typeof data.composed_image === "string" ? data.composed_image : null,
    first_frame_image: typeof data.first_frame_image === "string" ? data.first_frame_image : null,
    local_path: typeof data.local_path === "string" ? data.local_path : null,
  };
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

export function listScenes(projectId: number, episodeId?: number | null): Promise<SceneAsset[]> {
  const q =
    episodeId != null && Number.isFinite(episodeId)
      ? `?episode_id=${encodeURIComponent(String(episodeId))}`
      : "";
  return jsonRequest(`/projects/${projectId}/scenes${q}`);
}

export function generateSceneAsset(body: {
  scene_id: number;
  prompt?: string;
  art_style_id?: number;
  username?: string;
  node_id?: number;
  resolution?: AssetResolution;
  /** 采样步数 12–50，仅影响场景底板图，与视频无关 */
  steps?: number;
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
  /** 采样步数 12–50，仅影响道具定妆图，与视频无关 */
  steps?: number;
  extra?: string;
}): Promise<AssetGenerationResult> {
  return jsonRequest("/assets/prop/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** 道具优先方形/横屏特写 */
export const PROP_ASSET_RESOLUTIONS: { value: AssetResolution; label: string }[] = [
  { value: "square_1024x1024", label: "方形 1024×1024（默认·物件特写）" },
  { value: "uhd_landscape_1280x720", label: "超清横屏 1280×720" },
  { value: "landscape_1024x576", label: "横屏 1024×576" },
  { value: "fast_landscape_864x480", label: "快速横屏 864×480（预览）" },
  { value: "portrait_768x1024", label: "竖屏 768×1024" },
];

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
  segment_key?: string | null;
  segment_title?: string | null;
  segment_part?: number | null;
  segment_total?: number | null;
  trim_in?: number | null;
  trim_out?: number | null;
  selected_video_id?: number | null;
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
  status: "completed" | "failed" | "blocked" | "pending";
  merged_url?: string | null;
  duration?: number;
  error?: string | null;
  blocked?: boolean;
  storyboard_id?: number | null;
  clip_index?: number;
  async?: boolean;
  job?: ProductionJob;
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

export async function exportTimeline(
  episodeId: number,
  options?: { username?: string; async_mode?: boolean },
): Promise<TimelineExportResult> {
  const response = await apiFetch(`/timeline/${episodeId}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: options?.username,
      async_mode: options?.async_mode ?? false,
    }),
  });
  const data = await response.json();
  if (response.status === 451) return data as TimelineExportResult;
  if (!response.ok) {
    return { status: "failed", merged_url: null, error: data.error ?? data.detail ?? "导出失败" };
  }
  return data as TimelineExportResult;
}

export function getSetupWizard(): Promise<{
  ready: boolean;
  production_ready: boolean;
  version: string;
  steps: { id: string; title: string; ok: boolean; detail: string; fix?: string | null; gpu_hint?: string }[];
  next_actions: string[];
}> {
  return jsonRequest("/setup/wizard");
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
  first_frame_image?: string | null;
  last_frame_image?: string | null;
  video_url?: string | null;
  status: string;
  /** 运镜段落：连续多镜共享 */
  segment_key?: string | null;
  segment_title?: string | null;
  segment_part?: number | null;
  segment_total?: number | null;
  in_segment?: boolean;
  reference_mode: "auto" | "manual";
  reference_images: StoryboardReference[];
  scene_id?: number | null;
  readiness?: {
    ref_count: number;
    has_scene_ref: boolean;
    has_character_ref: boolean;
    has_image: boolean;
    missing_cast_images: string[];
    scene_bound: boolean;
  };
}

export interface StoryboardSegmentSummary {
  segment_key: string;
  segment_title: string | null;
  storyboard_ids: number[];
  storyboard_numbers: number[];
  count: number;
}

export interface StoryboardSplitResult {
  source_storyboard_id: number;
  segment_key: string;
  segment_title: string;
  parts: number;
  storyboard_ids: number[];
  storyboard_numbers: number[];
  storyboards: Storyboard[];
  needs_reimage?: boolean;
  /** 拆镜后有台词子镜需要/已尝试重配音 */
  needs_tts?: boolean;
  needs_tts_ids?: number[];
  tts_results?: Array<{ storyboard_id: number; status: string; tts_audio_url?: string | null; error?: string }>;
  tts_ok?: number;
  tts_fail?: number;
  message?: string;
}

export interface StoryboardReference {
  url: string;
  preview_url: string;
  kind: "scene" | "continuity" | "previous" | "character" | "prop" | "manual";
  label: string;
  source_storyboard_id: number | null;
  asset_name?: string | null;
  character_id?: number | null;
  prop_id?: number | null;
  is_speaker?: boolean;
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
  /** 后端回写：用于前端校验「出的就是这镜」 */
  storyboard_id?: number;
  storyboard_number?: number;
  warn?: boolean;
  level?: string;
  message?: string;
  hits?: ComplianceHit[];
  banned?: boolean;
}

export function listStoryboards(episodeId: number): Promise<Storyboard[]> {
  return jsonRequest(`/storyboard?episode_id=${episodeId}`);
}

export function listStoryboardSegments(episodeId: number): Promise<{ episode_id: number; segments: StoryboardSegmentSummary[] }> {
  return jsonRequest(`/storyboard/segments?episode_id=${episodeId}`);
}

/** 长镜拆解：一镜 → 连续 2–4 个 3–5 秒子镜 */
export function splitStoryboard(
  storyboardId: number,
  body?: { parts?: number; use_llm?: boolean; username?: string },
): Promise<StoryboardSplitResult> {
  return jsonRequest(`/storyboard/${storyboardId}/split`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
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
    auto_summary?: string;
    changes: StoryboardRemediationChange[];
    deterministic_count?: number;
    llm_count?: number;
    unresolved?: string[];
    needs_semantic_rereview?: boolean;
    note?: string;
  };
  original_issues?: StoryboardReviewIssue[];
}

export interface StoryboardRemediationChange {
  storyboard_id: number;
  storyboard_number: number;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  source?: string;
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
): Promise<{
  review: StoryboardReviewReport;
  changed_count: number;
  changes: StoryboardRemediationChange[];
  residual_count?: number;
  unresolved?: string[];
}> {
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
/** final=MiniMax H3 Turbo 多参考 r2v 定稿成片（test 已废弃，后端回落 final） */
export type VideoQualityMode = "test" | "final";

export interface VideoGenResult {
  video_id: number;
  storyboard_id: number;
  status: string;
  video_url: string | null;
  prompt: string | null;
  error_msg?: string | null;
  duration?: number | null;
  resolution?: string | null;
  model?: string | null;
  quality_mode?: VideoQualityMode | string | null;
  /** 配音长于镜长等提示（成功时也可能有） */
  warning?: string | null;
  tts_duration?: number | null;
  kept_embedded_audio?: boolean;
  note?: string | null;
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
  /** 连续运镜：用上一镜视频尾帧作本镜 i2v 起点，默认 true */
  use_prev_last_frame?: boolean;
  /** final=H3 r2v 定稿。默认 final（test 已废弃） */
  quality_mode?: VideoQualityMode;
  /** 异步入队，推荐批量长任务 */
  async_mode?: boolean;
}

export interface BatchVideoGenInput {
  storyboard_ids: number[];
  model?: string;
  reference_mode?: string;
  node_id?: number;
  username?: string;
  duration?: number;
  resolution?: string;
  use_prev_last_frame?: boolean;
  quality_mode?: VideoQualityMode;
  async_mode?: boolean;
}

export interface ProductionJob {
  id: number;
  job_type: string;
  status: string;
  progress: number;
  message?: string | null;
  error_msg?: string | null;
  error_code?: string | null;
  drama_id?: number | null;
  episode_id?: number | null;
  storyboard_id?: number | null;
  result?: Record<string, unknown> | null;
  cancel_requested?: boolean;
  created_at?: string | null;
}

export interface NovelEventRow {
  id: number;
  drama_id: number;
  chapter_id?: number | null;
  event_number: number;
  title?: string | null;
  summary?: string | null;
  characters?: string | null;
  location?: string | null;
  conflict?: string | null;
  emotion?: string | null;
  key_dialogue?: string | null;
  episode_id?: number | null;
  status?: string | null;
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

export function generateVideo(body: VideoGenInput): Promise<VideoGenResult & { async?: boolean; job?: ProductionJob }> {
  return jsonRequest("/video/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function batchGenerateVideo(
  body: BatchVideoGenInput,
): Promise<{
  async?: boolean;
  job?: ProductionJob;
  results?: { storyboard_id: number; status: string; video_url?: string | null; video_id?: number; error?: string; hits?: ComplianceHit[] }[];
  quality_mode?: string;
}> {
  return jsonRequest("/video/batch-generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function selectVideoVersion(storyboardId: number, videoId: number): Promise<{ storyboard_id: number; video_id: number; video_url: string }> {
  return jsonRequest("/video/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ storyboard_id: storyboardId, video_id: videoId }),
  });
}

export function setVideoTrim(storyboardId: number, trim_in?: number | null, trim_out?: number | null): Promise<{ storyboard_id: number; trim_in?: number; trim_out?: number }> {
  return jsonRequest("/video/trim", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ storyboard_id: storyboardId, trim_in, trim_out }),
  });
}

export function listJobs(params?: { status?: string; drama_id?: number; limit?: number }): Promise<{ jobs: ProductionJob[] }> {
  const q = new URLSearchParams();
  if (params?.status) q.set("status", params.status);
  if (params?.drama_id != null) q.set("drama_id", String(params.drama_id));
  if (params?.limit != null) q.set("limit", String(params.limit));
  const suffix = q.toString() ? `?${q}` : "";
  return jsonRequest(`/jobs${suffix}`);
}

export function getJob(jobId: number): Promise<ProductionJob> {
  return jsonRequest(`/jobs/${jobId}`);
}

export function cancelJob(jobId: number): Promise<ProductionJob> {
  return jsonRequest(`/jobs/${jobId}/cancel`, { method: "POST" });
}

// ===== 小白简易模式（自研） =====
export function easyChecklist(): Promise<{
  ready: boolean;
  version?: string;
  tips: string[];
  steps: { id: string; title: string; ok: boolean; detail: string; fix?: string | null }[];
  headline: string;
  summary: string;
  paths?: { llm?: boolean; comfy?: boolean; ffmpeg?: boolean; workflows?: boolean };
  stack?: { text?: string; image_video?: string };
}> {
  return jsonRequest("/easy/checklist");
}

export function easyMoods(): Promise<{ moods: { id: string; label: string }[] }> {
  return jsonRequest("/easy/moods");
}

export function easyBootstrap(body: {
  title?: string;
  text: string;
  art_style_id?: number;
  username?: string;
}): Promise<{
  drama_id: number;
  episode_id: number;
  title: string;
  has_script: boolean;
  has_content: boolean;
}> {
  return jsonRequest("/easy/bootstrap", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function easyPipeline(body: {
  episode_id: number;
  username?: string;
  skip_existing?: boolean;
  do_export?: boolean;
  transition?: string;
  bgm_mood?: string;
  auto_bgm?: boolean;
  async_mode?: boolean;
}): Promise<{
  async?: boolean;
  status?: string;
  job?: ProductionJob;
  ok?: boolean;
  merged_url?: string | null;
  error?: string | null;
  steps?: { step: string; status: string; message: string; detail?: Record<string, unknown> }[];
  drama_id?: number;
  episode_id?: number;
}> {
  const response = await apiFetch("/easy/pipeline", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok && response.status !== 451 && response.status !== 502) {
    const detail = data.detail;
    const msg =
      typeof detail === "string"
        ? detail
        : detail?.message || data.error || data.message || `请求失败 (${response.status})`;
    throw new Error(msg);
  }
  return data;
}

export function enqueueJob(body: {
  job_type: string;
  payload?: Record<string, unknown>;
  drama_id?: number;
  episode_id?: number;
  storyboard_id?: number;
  username?: string;
  message?: string;
}): Promise<ProductionJob> {
  return jsonRequest("/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function listNovelEvents(dramaId: number): Promise<{ chapters: unknown[]; events: NovelEventRow[] }> {
  return jsonRequest(`/events/${dramaId}`);
}

export function extractNovelEvents(body: {
  drama_id: number;
  text: string;
  chapter_title?: string;
  chapter_number?: number;
  async_mode?: boolean;
  username?: string;
}): Promise<Record<string, unknown>> {
  return jsonRequest("/events/extract", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function adaptNovelEvents(body: {
  drama_id: number;
  event_ids: number[];
  episode_id?: number;
  episode_title?: string;
  async_mode?: boolean;
  username?: string;
}): Promise<Record<string, unknown>> {
  return jsonRequest("/events/adapt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getProjectManuals(dramaId: number): Promise<{
  director_manual: string;
  visual_manual: string;
  banned_elements: string;
  memory: Record<string, unknown>;
  model_map: Record<string, unknown>;
  model_map_resolved?: Record<string, unknown>;
}> {
  return jsonRequest(`/projects/${dramaId}/manuals`);
}

export function saveProjectManuals(
  dramaId: number,
  body: {
    director_manual?: string;
    visual_manual?: string;
    banned_elements?: string;
    memory?: Record<string, unknown>;
    model_map?: Record<string, unknown>;
  },
): Promise<Record<string, unknown>> {
  return jsonRequest(`/projects/${dramaId}/manuals`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function refreshProjectMemory(dramaId: number): Promise<{ memory: Record<string, unknown> }> {
  return jsonRequest(`/projects/${dramaId}/memory/refresh`, { method: "POST" });
}

export function backupProject(dramaId: number): Promise<{ filename: string; bytes: number; path: string }> {
  return jsonRequest(`/projects/${dramaId}/backup`, { method: "POST" });
}

export function runProductionSupervise(episodeId: number, asyncMode = false): Promise<Record<string, unknown>> {
  return jsonRequest("/production/supervise", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ episode_id: episodeId, async_mode: asyncMode }),
  });
}

export function diagnoseCompute(nodeId?: number): Promise<Record<string, unknown>> {
  const q = nodeId != null ? `?node_id=${nodeId}` : "";
  return jsonRequest(`/compute/diagnose${q}`);
}

export function listSkills(category?: string): Promise<{ name: string; display_name: string; category: string; description: string }[]> {
  const q = category ? `?category=${encodeURIComponent(category)}` : "";
  return jsonRequest(`/skills${q}`);
}

export function getSkill(category: string, name: string): Promise<{ name: string; content: string; raw?: string; display_name: string }> {
  return jsonRequest(`/skills/${category}/${name}`);
}

export function saveSkill(category: string, name: string, content: string): Promise<Record<string, unknown>> {
  return jsonRequest(`/skills/${category}/${name}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
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
  if (r.ok) {
    return {
      status: r.status,
      image_url: data.image_url,
      storyboard_id: data.storyboard_id ?? storyboardId,
      storyboard_number: data.storyboard_number,
      warn: data.warn,
      hits: data.warn_hits,
    };
  }
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

export function listArtStyleSkills(): Promise<SkillMeta[]> {
  return jsonRequest("/skills/art-styles");
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
