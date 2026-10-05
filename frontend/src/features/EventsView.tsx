import { useEffect, useState } from "react";
import { GitBranch, Loader2, Sparkles, Wand2 } from "lucide-react";
import {
  adaptNovelEvents,
  extractNovelEvents,
  listNovelEvents,
  type NovelEventRow,
} from "../api/client";

export default function EventsView({
  dramaId,
  username,
  onGotoScript,
  onGotoStoryboard,
}: {
  dramaId: number | null;
  username: string;
  onGotoScript?: () => void;
  onGotoStoryboard?: () => void;
}) {
  const [events, setEvents] = useState<NovelEventRow[]>([]);
  const [text, setText] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const reload = () => {
    if (dramaId == null) return;
    listNovelEvents(dramaId)
      .then((r) => setEvents(r.events || []))
      .catch(() => setEvents([]));
  };

  useEffect(() => {
    reload();
  }, [dramaId]);

  if (dramaId == null) {
    return (
      <div className="feature-view">
        <div className="feature-header">
          <h2>事件图谱</h2>
          <p>请先在「项目」打开一个项目</p>
        </div>
      </div>
    );
  }

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const extract = async () => {
    if (!text.trim()) {
      setNotice("请粘贴小说章节或全文");
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const r = await extractNovelEvents({
        drama_id: dramaId,
        text,
        username,
        async_mode: false,
      });
      setNotice(`已提取 ${String(r.event_count ?? 0)} 个事件（无 LLM 时用段落启发式）`);
      reload();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "提取失败");
    } finally {
      setBusy(false);
    }
  };

  const adapt = async () => {
    if (!selected.size) {
      setNotice("请勾选要改编的事件");
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const r = await adaptNovelEvents({
        drama_id: dramaId,
        event_ids: [...selected],
        username,
      });
      setNotice(
        `已写入分集 #${String(r.episode_id)}「${String(r.title || "")}」。可去剧本核对，或直接拆分镜。`,
      );
      reload();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "改编失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="feature-view" style={{ display: "flex", flexDirection: "column", minHeight: 0, flex: 1 }}>
      <div className="feature-header">
        <div>
          <h2>
            <GitBranch size={18} style={{ verticalAlign: -3 }} /> 事件图谱
          </h2>
          <p>小说 → 事件 → 按事件改编（章节事件驱动，减少长文丢信息）</p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {onGotoScript ? (
            <button type="button" className="btn-secondary" style={{ width: "auto" }} onClick={onGotoScript}>
              去剧本
            </button>
          ) : null}
          {onGotoStoryboard ? (
            <button type="button" className="btn-secondary" style={{ width: "auto" }} onClick={onGotoStoryboard}>
              去镜头脚本
            </button>
          ) : null}
        </div>
      </div>
      {notice && <div className="notice-bar" style={{ margin: "0 12px 8px" }}>{notice}</div>}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, padding: 12, minHeight: 0, flex: 1 }}>
        <section style={{ display: "flex", flexDirection: "column", gap: 8, minHeight: 0 }}>
          <label style={{ fontSize: 12, color: "var(--text2)" }}>粘贴小说 / 章节</label>
          <textarea
            style={{ flex: 1, minHeight: 220, resize: "vertical" }}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="粘贴原文后点「提取事件」。已配置 LLM 时结构化更好；否则按段落切分。"
          />
          <button type="button" className="btn-primary" disabled={busy} onClick={() => void extract()}>
            {busy ? <Loader2 className="spin" size={14} /> : <Sparkles size={14} />} 提取事件
          </button>
        </section>
        <section style={{ display: "flex", flexDirection: "column", gap: 8, minHeight: 0, overflow: "hidden" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text2)" }}>事件列表 · {events.length}</span>
            <div style={{ flex: 1 }} />
            <button type="button" className="btn-primary" disabled={busy || !selected.size} onClick={() => void adapt()}>
              {busy ? <Loader2 className="spin" size={14} /> : <Wand2 size={14} />} 改编为剧本
            </button>
          </div>
          <div style={{ overflow: "auto", flex: 1, border: "1px solid var(--border)", borderRadius: 8, padding: 6 }}>
            {!events.length && <div style={{ color: "var(--text3)", padding: 12 }}>暂无事件</div>}
            {events.map((ev) => (
              <label
                key={ev.id}
                style={{
                  display: "block",
                  padding: "8px 10px",
                  borderBottom: "1px solid var(--border2)",
                  cursor: "pointer",
                  background: selected.has(ev.id) ? "rgba(43,178,76,0.08)" : "transparent",
                }}
              >
                <input type="checkbox" checked={selected.has(ev.id)} onChange={() => toggle(ev.id)} />{" "}
                <strong>
                  #{ev.event_number} {ev.title}
                </strong>
                <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 4 }}>
                  {ev.summary || "—"} · {ev.status}
                  {ev.episode_id ? ` · 已改编→集${ev.episode_id}` : ""}
                </div>
              </label>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
