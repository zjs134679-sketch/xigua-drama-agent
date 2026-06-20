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
}

export async function generateScriptDraft(content: string): Promise<ScriptDraftResponse> {
  const r = await fetch(`${API}/script/draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  const data = await r.json();
  if (r.ok) {
    return { status: r.status, script: data.script_content, warn: data.warn, hits: data.hits };
  }
  const detail = data.detail;
  if (detail && typeof detail === "object") {
    return { status: r.status, level: detail.level, message: detail.message, hits: detail.hits };
  }
  return { status: r.status, message: typeof detail === "string" ? detail : "生成失败" };
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
