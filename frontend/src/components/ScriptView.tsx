import { useEffect, useState } from "react";
import { AlertTriangle, FileText, Loader2, Wand2 } from "lucide-react";
import {
  generateEpisodeScript,
  generateScriptDraft,
  getEpisode,
  type ComplianceHit,
  type EpisodeSummary,
  type Project,
} from "../api/client";

function HighlightedText({ text, hits }: { text: string; hits: ComplianceHit[] }) {
  const words = [...new Set(hits.map((hit) => hit.word).filter(Boolean))];
  if (words.length === 0) return <>{text}</>;
  const escaped = words.map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`(${escaped.join("|")})`, "gi");
  const lowered = new Set(words.map((word) => word.toLocaleLowerCase()));
  return (
    <>
      {text.split(pattern).map((part, index) =>
        lowered.has(part.toLocaleLowerCase()) ? (
          <mark key={`${part}-${index}`} style={{ background: "rgba(224,160,27,0.35)", color: "inherit" }}>{part}</mark>
        ) : (
          part
        ),
      )}
    </>
  );
}

export default function ScriptView({
  username,
  current,
  onBanned,
  onGoto,
}: {
  username: string;
  current?: { drama: Project; episode: EpisodeSummary } | null;
  onBanned?: () => void;
  onGoto?: (view: string) => void;
}) {
  const episode = current?.episode ?? null;
  const [novel, setNovel] = useState(
    "夜里，林医生还在值班室翻看病历，窗外下着大雨。护士小张敲门进来，递上一杯热咖啡。",
  );
  const [script, setScript] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<{ level?: string; message?: string; hits?: ComplianceHit[] } | null>(null);
  const [warningHits, setWarningHits] = useState<ComplianceHit[]>([]);
  const [savedHint, setSavedHint] = useState(false);

  // 选中分集时，载入它的小说原文与已存剧本
  useEffect(() => {
    if (!episode) return;
    let alive = true;
    setErr(null);
    setScript("");
    setWarningHits([]);
    getEpisode(episode.id)
      .then((d) => {
        if (!alive) return;
        setNovel(d.content ?? "");
        setScript(d.script_content ?? "");
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [episode?.id]);

  const run = async () => {
    setLoading(true);
    setErr(null);
    setScript("");
    setWarningHits([]);
    setSavedHint(false);
    try {
      const res = episode
        ? await generateEpisodeScript(episode.id, username) // 落库版
        : await generateScriptDraft(novel, username);       // 快速试写
      if (res.status === 200 && res.script) {
        setScript(res.script);
        if (episode) setSavedHint(true);
        if (res.warn) setWarningHits(res.hits ?? []);
      } else {
        setErr({ level: res.level, message: res.message, hits: res.hits });
        if (res.banned) onBanned?.();
      }
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
        {episode ? (
          <span style={{ fontSize: 11, color: "var(--text2)" }}>
            {current?.drama.title} · 第{episode.episode_number}集 {episode.title}
          </span>
        ) : (
          <span style={{ fontSize: 11, color: "var(--text3)" }}>小说 → 编剧 Agent → 格式化剧本（快速试写，未关联项目）</span>
        )}
        <div style={{ flex: 1 }} />
        {script && onGoto ? (
          <>
            <button type="button" className="btn-secondary" style={{ width: "auto", padding: "6px 12px" }} onClick={() => onGoto("characters")}>
              下一步：提取/出角色
            </button>
            <button type="button" className="btn-secondary" style={{ width: "auto", padding: "6px 12px" }} onClick={() => onGoto("storyboard")}>
              去镜头脚本
            </button>
            <button type="button" className="btn-secondary" style={{ width: "auto", padding: "6px 12px" }} onClick={() => onGoto("timeline")}>
              去成片
            </button>
          </>
        ) : null}
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
              <>
                {warningHits.length > 0 && (
                  <div style={{ border: "1px solid var(--amber)", borderRadius: 8, padding: 10, marginBottom: 10, color: "var(--amber)", fontSize: 12 }}>
                    <div style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 500 }}>
                      <AlertTriangle size={15} /> 黄线提示 · 建议替换高亮内容
                    </div>
                    <div style={{ marginTop: 5, color: "var(--text2)" }}>
                      命中：{warningHits.map((hit, index) => (
                        <mark key={`${hit.word}-${index}`} style={{ background: "rgba(224,160,27,0.3)", color: "inherit" }}>
                          {hit.word}({hit.category}){index < warningHits.length - 1 ? "、" : ""}
                        </mark>
                      ))}
                    </div>
                  </div>
                )}
                <HighlightedText text={script} hits={warningHits} />
              </>
            ) : (
              <span style={{ color: "var(--text3)" }}>点击右上「生成剧本」，DeepSeek 会按方法论输出格式化剧本…</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
