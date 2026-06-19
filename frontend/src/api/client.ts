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
