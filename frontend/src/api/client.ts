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
