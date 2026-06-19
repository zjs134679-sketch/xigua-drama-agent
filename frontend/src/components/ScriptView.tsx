import { useState } from "react";
import { AlertTriangle, FileText, Loader2, Wand2 } from "lucide-react";
import { generateScriptDraft, type ComplianceHit } from "../api/client";

export default function ScriptView() {
  const [novel, setNovel] = useState(
    "夜里，林医生还在值班室翻看病历，窗外下着大雨。护士小张敲门进来，递上一杯热咖啡。",
  );
  const [script, setScript] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<{ level?: string; message?: string; hits?: ComplianceHit[] } | null>(null);

  const run = async () => {
    setLoading(true);
    setErr(null);
    setScript("");
    try {
      const res = await generateScriptDraft(novel);
      if (res.status === 200 && res.script) setScript(res.script);
      else setErr({ level: res.level, message: res.message, hits: res.hits });
    } catch {
      setErr({ message: "请求失败，后端是否在运行？" });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
        <FileText size={15} color="var(--text2)" />
        <span style={{ fontSize: 12, fontWeight: 500 }}>剧本工作台</span>
        <span style={{ fontSize: 11, color: "var(--text3)" }}>小说 → 编剧 Agent → 格式化剧本</span>
        <div style={{ flex: 1 }} />
        <button className="btn-primary" style={{ width: "auto", padding: "6px 14px", opacity: loading ? 0.7 : 1 }} onClick={run} disabled={loading}>
          {loading ? (
            <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><Loader2 size={14} className="spin" /> 生成中…</span>
          ) : (
            <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><Wand2 size={14} /> 生成剧本</span>
          )}
        </button>
      </div>

      <div style={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1, background: "var(--border)" }}>
        <div style={{ background: "var(--bg)", display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{ padding: "6px 12px", fontSize: 11, color: "var(--text3)", borderBottom: "1px solid var(--border)" }}>小说原文</div>
          <textarea
            value={novel}
            onChange={(e) => setNovel(e.target.value)}
            placeholder="粘贴或输入小说原文…"
            style={{ flex: 1, border: "none", borderRadius: 0, background: "transparent", resize: "none", padding: 12, fontSize: 13, lineHeight: 1.7 }}
          />
        </div>

        <div style={{ background: "var(--panel2)", display: "flex", flexDirection: "column", minHeight: 0, overflow: "auto" }}>
          <div style={{ padding: "6px 12px", fontSize: 11, color: "var(--text3)", borderBottom: "1px solid var(--border)" }}>生成剧本</div>
          <div style={{ flex: 1, padding: 12, fontSize: 13, lineHeight: 1.7, whiteSpace: "pre-wrap", color: "var(--text)" }}>
            {err ? (
              <div style={{ border: `1px solid ${err.level === "red" ? "var(--red)" : "var(--amber)"}`, borderRadius: 8, padding: 12 }}>
                <div style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 500, color: err.level === "red" ? "var(--red-t)" : "var(--amber)" }}>
                  <AlertTriangle size={15} /> {err.level === "red" ? "红线拦截" : "无法生成"}
                </div>
                <div style={{ marginTop: 6, color: "var(--text2)", fontSize: 12, whiteSpace: "normal" }}>{err.message}</div>
                {err.hits && err.hits.length > 0 && (
                  <div style={{ marginTop: 6, fontSize: 12, color: "var(--text2)" }}>
                    命中：{err.hits.map((h) => `${h.word}(${h.category})`).join("、")}
                  </div>
                )}
              </div>
            ) : script ? (
              script
            ) : (
              <span style={{ color: "var(--text3)" }}>点击右上「生成剧本」，DeepSeek 会按方法论输出格式化剧本…</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
