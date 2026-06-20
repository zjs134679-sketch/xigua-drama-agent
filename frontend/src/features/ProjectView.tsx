import { useEffect, useState } from "react";
import { AlertTriangle, BookUp, FileText, FolderPlus, Loader2, Plus, Clapperboard } from "lucide-react";
import {
  createDrama,
  createEpisode,
  importNovel,
  listEpisodes,
  listProjects,
  type EpisodeSummary,
  type Project,
} from "../api/client";

const STYLE_OPTIONS = [
  { value: "realistic", label: "写实电影感" },
  { value: "anime", label: "动漫" },
  { value: "ink", label: "国风水墨" },
  { value: "3d", label: "3D 渲染" },
];

export default function ProjectView({
  current,
  username,
  onOpenEpisode,
}: {
  current: { drama: Project; episode: EpisodeSummary } | null;
  username: string;
  onOpenEpisode: (drama: Project, episode: EpisodeSummary) => void;
}) {
  const [dramas, setDramas] = useState<Project[]>([]);
  const [selected, setSelected] = useState<Project | null>(null);
  const [episodes, setEpisodes] = useState<EpisodeSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 新建项目
  const [dTitle, setDTitle] = useState("");
  const [dGenre, setDGenre] = useState("");
  const [dStyle, setDStyle] = useState("realistic");
  const [creatingD, setCreatingD] = useState(false);

  // 新建分集
  const [eTitle, setETitle] = useState("");
  const [eContent, setEContent] = useState("");
  const [creatingE, setCreatingE] = useState(false);

  // 导入整本小说
  const [showImport, setShowImport] = useState(false);
  const [novel, setNovel] = useState("");
  const [importing, setImporting] = useState(false);
  const [importMsg, setImportMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const refreshDramas = async (selectId?: number) => {
    const rows = await listProjects();
    setDramas(rows);
    if (selectId != null) {
      const hit = rows.find((d) => d.id === selectId);
      if (hit) selectDrama(hit);
    } else if (!selected && rows.length > 0) {
      selectDrama(current?.drama ?? rows[0]);
    }
  };

  useEffect(() => {
    refreshDramas().catch((e) => setErr(e instanceof Error ? e.message : "加载项目失败"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectDrama = async (d: Project) => {
    setSelected(d);
    setEpisodes([]);
    setLoading(true);
    try {
      setEpisodes(await listEpisodes(d.id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "加载分集失败");
    } finally {
      setLoading(false);
    }
  };

  const addDrama = async () => {
    if (!dTitle.trim()) return;
    setCreatingD(true);
    setErr(null);
    try {
      const created = await createDrama({ title: dTitle.trim(), genre: dGenre.trim() || null, style: dStyle });
      setDTitle("");
      setDGenre("");
      await refreshDramas(created.id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "新建项目失败");
    } finally {
      setCreatingD(false);
    }
  };

  const addEpisode = async () => {
    if (!selected || !eTitle.trim()) return;
    setCreatingE(true);
    setErr(null);
    try {
      const nextNo = (episodes.reduce((m, e) => Math.max(m, e.episode_number), 0) || 0) + 1;
      await createEpisode(selected.id, { episode_number: nextNo, title: eTitle.trim(), content: eContent.trim() || null });
      setETitle("");
      setEContent("");
      setEpisodes(await listEpisodes(selected.id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "新建分集失败");
    } finally {
      setCreatingE(false);
    }
  };

  const doImport = async () => {
    if (!selected || !novel.trim()) return;
    setImporting(true);
    setImportMsg(null);
    try {
      const res = await importNovel(selected.id, novel.trim(), username);
      if (res.status === 200) {
        setImportMsg({ ok: true, text: `已自动分集：新增 ${res.created} 集` });
        setNovel("");
        setShowImport(false);
        setEpisodes(await listEpisodes(selected.id));
      } else {
        setImportMsg({ ok: false, text: res.message || "导入失败" });
      }
    } catch (e) {
      setImportMsg({ ok: false, text: e instanceof Error ? e.message : "导入失败" });
    } finally {
      setImporting(false);
    }
  };

  const label = { fontSize: 11, color: "var(--text3)", margin: "0 0 4px" } as const;

  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
        <Clapperboard size={15} color="var(--text2)" />
        <span style={{ fontSize: 12, fontWeight: 500 }}>项目</span>
        <span style={{ fontSize: 11, color: "var(--text3)" }}>短剧 → 分集 → 进入创作</span>
      </div>

      {err && (
        <div style={{ margin: "8px 12px 0", padding: "6px 10px", borderRadius: 6, border: "1px solid var(--red)", color: "var(--red-t)", fontSize: 12 }}>
          {err}
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: "300px 1fr", gap: 1, background: "var(--border)" }}>
        {/* 左：项目列表 + 新建 */}
        <div style={{ background: "var(--bg)", display: "flex", flexDirection: "column", minHeight: 0, overflow: "auto" }}>
          <div style={{ padding: "8px 12px", display: "flex", flexDirection: "column", gap: 6, borderBottom: "1px solid var(--border)" }}>
            <p style={label}>新建短剧项目</p>
            <input value={dTitle} onChange={(e) => setDTitle(e.target.value)} placeholder="剧名，如：时光邮局" />
            <input value={dGenre} onChange={(e) => setDGenre(e.target.value)} placeholder="题材（可选），如：都市/悬疑" />
            <select value={dStyle} onChange={(e) => setDStyle(e.target.value)}>
              {STYLE_OPTIONS.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
            <button className="btn-primary" style={{ padding: "6px 12px", opacity: creatingD ? 0.7 : 1 }} onClick={addDrama} disabled={creatingD || !dTitle.trim()}>
              {creatingD ? <Loader2 size={14} className="spin" /> : <FolderPlus size={14} />} 新建项目
            </button>
          </div>
          <div style={{ padding: "6px 8px" }}>
            {dramas.length === 0 ? (
              <p style={{ fontSize: 12, color: "var(--text3)", padding: 8 }}>还没有项目，先在上方新建一个。</p>
            ) : (
              dramas.map((d) => (
                <button
                  key={d.id}
                  onClick={() => selectDrama(d)}
                  style={{
                    width: "100%", textAlign: "left", padding: "8px 10px", marginBottom: 4, borderRadius: 6, cursor: "pointer",
                    border: selected?.id === d.id ? "1px solid var(--green)" : "1px solid var(--border2)",
                    background: selected?.id === d.id ? "rgba(43,178,76,0.10)" : "var(--panel)",
                    color: "var(--text)",
                  }}
                >
                  <div style={{ fontSize: 13, fontWeight: 500 }}>{d.title}</div>
                  <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 2 }}>
                    {d.genre || "未分类"} · {STYLE_OPTIONS.find((s) => s.value === d.style)?.label ?? d.style ?? "—"}
                  </div>
                </button>
              ))
            )}
          </div>
        </div>

        {/* 右：分集列表 + 新建 */}
        <div style={{ background: "var(--panel2)", display: "flex", flexDirection: "column", minHeight: 0, overflow: "auto" }}>
          {!selected ? (
            <p style={{ fontSize: 12, color: "var(--text3)", padding: 16 }}>选择左侧一个项目以管理分集。</p>
          ) : (
            <>
              <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 14, fontWeight: 600 }}>{selected.title}</div>
                  <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 2 }}>共 {episodes.length} 集</div>
                </div>
                <button
                  className="btn-secondary"
                  style={{ width: "auto", padding: "6px 12px" }}
                  onClick={() => { setShowImport((v) => !v); setImportMsg(null); }}
                >
                  <BookUp size={13} /> 导入小说
                </button>
              </div>

              {showImport && (
                <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", background: "var(--bg)", display: "flex", flexDirection: "column", gap: 6 }}>
                  <p style={label}>粘贴整本小说，自动按「第X章/节/回」分集（无标题则按长度切）</p>
                  <textarea
                    value={novel}
                    onChange={(e) => setNovel(e.target.value)}
                    rows={6}
                    placeholder={"第一章 启程\n少年背起行囊……\n\n第二章 古镇\n……"}
                    style={{ resize: "vertical", lineHeight: 1.6, fontSize: 12 }}
                  />
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <button className="btn-primary" style={{ width: "auto", padding: "6px 14px", opacity: importing ? 0.7 : 1 }} onClick={doImport} disabled={importing || !novel.trim()}>
                      {importing ? <Loader2 size={14} className="spin" /> : <BookUp size={14} />} 导入并分集
                    </button>
                    {importMsg && (
                      <span style={{ fontSize: 12, color: importMsg.ok ? "var(--green-t)" : "var(--red-t)", display: "inline-flex", gap: 4, alignItems: "center" }}>
                        {!importMsg.ok && <AlertTriangle size={13} />}{importMsg.text}
                      </span>
                    )}
                  </div>
                </div>
              )}

              <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
                {loading ? (
                  <div style={{ fontSize: 12, color: "var(--text3)", display: "flex", gap: 6, alignItems: "center" }}>
                    <Loader2 size={14} className="spin" /> 加载分集…
                  </div>
                ) : episodes.length === 0 ? (
                  <p style={{ fontSize: 12, color: "var(--text3)" }}>还没有分集，在下方添加第 1 集。</p>
                ) : (
                  episodes.map((ep) => (
                    <div key={ep.id} className="card" style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px" }}>
                      <span style={{ fontSize: 12, color: "var(--text3)", width: 34 }}>第{ep.episode_number}集</span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 13, color: "var(--text)" }}>{ep.title}</div>
                        <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 2, display: "flex", gap: 8 }}>
                          <span style={{ color: ep.has_content ? "var(--green-t)" : "var(--text3)" }}>{ep.has_content ? "✓ 有小说" : "○ 无小说"}</span>
                          <span style={{ color: ep.has_script ? "var(--green-t)" : "var(--text3)" }}>{ep.has_script ? "✓ 有剧本" : "○ 无剧本"}</span>
                        </div>
                      </div>
                      <button
                        className="btn-primary"
                        style={{ width: "auto", padding: "6px 14px" }}
                        onClick={() => onOpenEpisode(selected, ep)}
                      >
                        <FileText size={13} /> 进入创作
                      </button>
                    </div>
                  ))
                )}
              </div>

              <div style={{ marginTop: "auto", borderTop: "1px solid var(--border)", padding: 12, display: "flex", flexDirection: "column", gap: 6 }}>
                <p style={label}>新增分集</p>
                <input value={eTitle} onChange={(e) => setETitle(e.target.value)} placeholder="本集标题，如：旧信封的秘密" />
                <textarea
                  value={eContent}
                  onChange={(e) => setEContent(e.target.value)}
                  rows={3}
                  placeholder="粘贴本集小说原文（可稍后在剧本页补）…"
                  style={{ resize: "vertical", lineHeight: 1.6 }}
                />
                <button className="btn-secondary" style={{ width: "auto", padding: "6px 14px", opacity: creatingE ? 0.7 : 1 }} onClick={addEpisode} disabled={creatingE || !eTitle.trim()}>
                  {creatingE ? <Loader2 size={14} className="spin" /> : <Plus size={14} />} 添加分集
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
