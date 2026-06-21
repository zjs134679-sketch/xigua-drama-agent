import { useEffect, useState } from "react";
import { AlertTriangle, Image as ImageIcon, Layers, Loader2, Save, Sparkles, Wand2 } from "lucide-react";
import {
  batchGenerateStoryboardPrompts,
  generateStoryboardImage,
  generateStoryboards,
  listStoryboards,
  listComputeNodes,
  updateStoryboardPrompt,
  type ComplianceHit,
  type AssetResolution,
  type ComputeNodeRecord,
  type EpisodeSummary,
  type Project,
  type Storyboard,
} from "../api/client";
import BatchBar from "../components/BatchBar";
import AssetGenerationControls from "../components/AssetGenerationControls";
import AssetHistoryStrip from "../components/AssetHistoryStrip";
import AdditionalInstructionField from "../components/AdditionalInstructionField";
import { useBatchRun, type BatchOutcome } from "../components/useBatchRun";
import { useSelection } from "../components/useSelection";

export default function StoryboardView({
  current,
  username,
  onBanned,
}: {
  current: { drama: Project; episode: EpisodeSummary } | null;
  username: string;
  onBanned?: () => void;
}) {
  const episode = current?.episode ?? null;
  const [shots, setShots] = useState<Storyboard[]>([]);
  const [loading, setLoading] = useState(false);
  const [breaking, setBreaking] = useState(false);
  const [imaging, setImaging] = useState<number | null>(null);
  const [saving, setSaving] = useState<number | null>(null);
  const [polishing, setPolishing] = useState(false);
  const [prompts, setPrompts] = useState<Record<number, string>>({});
  const [nodes, setNodes] = useState<ComputeNodeRecord[]>([]);
  const [resolutions, setResolutions] = useState<Record<number, AssetResolution>>({});
  const [nodeIds, setNodeIds] = useState<Record<number, number | undefined>>({});
  const [err, setErr] = useState<{ message?: string; hits?: ComplianceHit[]; level?: string } | null>(null);
  const [extra, setExtra] = useState("");
  const sel = useSelection();
  const batch = useBatchRun();

  const seedPrompts = (rows: Storyboard[]) =>
    setPrompts(() => {
      const next: Record<number, string> = {};
      rows.forEach((s) => { next[s.id] = s.image_prompt ?? ""; });
      return next;
    });

  const load = async (epId: number) => {
    setLoading(true);
    try {
      const rows = await listStoryboards(epId);
      setShots(rows);
      seedPrompts(rows);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  };

  const savePrompt = async (sb: Storyboard) => {
    setSaving(sb.id);
    setErr(null);
    try {
      await updateStoryboardPrompt(sb.id, prompts[sb.id] ?? "");
    } catch {
      setErr({ message: "保存提示词失败" });
    } finally {
      setSaving(null);
    }
  };

  useEffect(() => {
    listComputeNodes().then(setNodes).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (episode) load(episode.id);
    else setShots([]);
    setErr(null);
    sel.clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [episode?.id]);

  const breakdown = async () => {
    if (!episode) return;
    setBreaking(true);
    setErr(null);
    try {
      const res = await generateStoryboards(episode.id, username);
      if (res.status === 200 && res.storyboards) {
        setShots(res.storyboards);
        seedPrompts(res.storyboards);
        sel.clear();
      } else {
        setErr({ message: res.message, hits: res.hits, level: res.level });
        if (res.banned) onBanned?.();
      }
    } catch {
      setErr({ message: "请求失败，后端是否在运行？" });
    } finally {
      setBreaking(false);
    }
  };

  // 单镜出图核心：用编辑后的提示词，结果合并回该镜；批量由调用方统一处理。
  const imageCore = async (sb: Storyboard): Promise<BatchOutcome> => {
    setImaging(sb.id);
    try {
      const res = await generateStoryboardImage(
        sb.id,
        username,
        undefined,
        prompts[sb.id] ?? undefined,
        {
          node_id: nodeIds[sb.id],
          resolution: resolutions[sb.id] ?? "landscape_1024x576",
          extra: extra.trim() || undefined,
        },
      );
      if (res.status === 200 && res.image_url) {
        setShots((prev) => prev.map((s) => (s.id === sb.id ? { ...s, image_url: res.image_url ?? null, status: "image_done" } : s)));
        return { ok: true };
      }
      return { ok: false, message: res.message, banned: res.banned };
    } catch {
      return { ok: false, message: "出图请求失败" };
    } finally {
      setImaging(null);
    }
  };

  const makeImage = async (sb: Storyboard) => {
    setErr(null);
    const outcome = await imageCore(sb);
    if (!outcome.ok) {
      setErr({ message: outcome.message });
      if (outcome.banned) onBanned?.();
    }
  };

  const polishSelectedPrompts = async () => {
    const picked = shots.filter((s) => sel.selected.has(s.id));
    if (!picked.length) return;
    setPolishing(true);
    setErr(null);
    try {
      const res = await batchGenerateStoryboardPrompts({
        ids: picked.map((s) => s.id),
        username,
      });
      if (res.updated > 0) {
        setPrompts((prev) => {
          const next = { ...prev };
          const byNumber = new Map<number, Storyboard>();
          shots.forEach((s) => { byNumber.set(s.storyboard_number, s); });
          res.prompts.forEach((p) => {
            const sb = [...byNumber.values()].find((s) => s.storyboard_number === p.number);
            if (sb) next[sb.id] = p.prompt;
          });
          return next;
        });
      }
    } catch (e: any) {
      setErr({ message: e?.message ?? "润色提示词失败" });
    } finally {
      setPolishing(false);
    }
  };

  const runBatch = async () => {
    const queue = shots.filter((s) => sel.selected.has(s.id));
    if (!queue.length) return;
    setErr(null);
    const byId = new Map(shots.map((s) => [s.id, s]));
    const res = await batch.run(queue.map((s) => s.id), (id) => imageCore(byId.get(id)!));
    if (!res) return;
    const ok = res.done - res.failed;
    if (res.banned) {
      setErr({ message: "账号已被封禁，已停止批量出图。", level: "red" });
      onBanned?.();
    } else if (res.stopped) {
      setErr({ message: `已停止：成功 ${ok}/${res.total}，失败 ${res.failed}。` });
    } else if (res.failed) {
      setErr({ message: `批量完成：成功 ${ok}、失败 ${res.failed}，共 ${res.total}。${res.lastMessage ? `（${res.lastMessage}）` : ""}` });
    }
  };

  if (!episode) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text3)", fontSize: 13 }}>
        请先在「项目」里选择一个分集，再来生成分镜。
      </div>
    );
  }

  const allIds = shots.map((s) => s.id);
  const ungeneratedIds = shots.filter((s) => !s.image_url).map((s) => s.id);
  const progressText = batch.progress
    ? `出图中 ${batch.progress.done}/${batch.progress.total}${batch.progress.failed ? `（失败 ${batch.progress.failed}）` : ""}`
    : null;

  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
        <Layers size={15} color="var(--text2)" />
        <span style={{ fontSize: 12, fontWeight: 500 }}>分镜台</span>
        <span style={{ fontSize: 11, color: "var(--text3)" }}>
          {current?.drama.title} · 第{episode.episode_number}集 · {shots.length} 个镜头
        </span>
        <div style={{ flex: 1 }} />
        <button
          className="btn-primary"
          style={{ width: "auto", padding: "6px 14px", opacity: breaking ? 0.7 : 1 }}
          onClick={breakdown}
          disabled={breaking || batch.running}
          title="用编剧剧本重新拆解分镜（会覆盖现有分镜）"
        >
          {breaking ? <Loader2 size={14} className="spin" /> : <Wand2 size={14} />} {shots.length ? "重拆分镜" : "一键生成分镜"}
        </button>
      </div>

      <AdditionalInstructionField value={extra} onChange={setExtra} disabled={batch.running} />

      {shots.length > 0 && (
        <BatchBar
          total={shots.length}
          ungeneratedCount={ungeneratedIds.length}
          selectedCount={sel.selected.size}
          onSelectAll={() => sel.replace(allIds)}
          onSelectUngenerated={() => sel.replace(ungeneratedIds)}
          onInvert={() => sel.invert(allIds)}
          onClear={() => sel.clear()}
          onRun={runBatch}
          onStop={batch.stop}
          running={batch.running}
          progressText={progressText}
          runLabel="批量出图"
        />
      )}

      {shots.length > 0 && sel.selected.size > 0 && !batch.running && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "4px 16px",
            borderBottom: "1px solid var(--border)",
            background: "var(--panel2)",
          }}
        >
          <span style={{ fontSize: 11, color: "var(--text3)" }}>已选 {sel.selected.size} 个分镜 ·</span>
          <button
            className="btn-secondary"
            style={{ fontSize: 11, padding: "4px 10px", display: "inline-flex", alignItems: "center", gap: 4, opacity: polishing ? 0.7 : 1 }}
            onClick={polishSelectedPrompts}
            disabled={polishing}
          >
            {polishing ? <Loader2 size={12} className="spin" /> : <Sparkles size={12} />}
            AI 润色提示词
          </button>
        </div>
      )}

      {err && (
        <div style={{ margin: "8px 12px 0", padding: "8px 10px", borderRadius: 6, border: `1px solid ${err.level === "red" ? "var(--red)" : "var(--amber)"}`, color: err.level === "red" ? "var(--red-t)" : "var(--amber)", fontSize: 12 }}>
          <div style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 500 }}>
            <AlertTriangle size={14} /> {err.level === "red" ? "红线拦截" : "提示"}
          </div>
          <div style={{ marginTop: 4, color: "var(--text2)" }}>{err.message}</div>
          {err.hits && err.hits.length > 0 && (
            <div style={{ marginTop: 4, color: "var(--text2)" }}>命中：{err.hits.map((h) => `${h.word}(${h.category})`).join("、")}</div>
          )}
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: 12 }}>
        {loading ? (
          <div style={{ color: "var(--text3)", fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}><Loader2 size={14} className="spin" /> 加载分镜…</div>
        ) : shots.length === 0 ? (
          <div style={{ color: "var(--text3)", fontSize: 13, padding: "24px 0", textAlign: "center" }}>
            还没有分镜。点右上「一键生成分镜」，编剧剧本会被拆成镜头清单。
            <div style={{ fontSize: 11, marginTop: 6 }}>（需要该分集已生成剧本）</div>
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 12 }}>
            {shots.map((s) => {
              const picked = sel.selected.has(s.id);
              return (
              <div key={s.id} className="card" style={{ display: "flex", flexDirection: "column", ...(picked ? { outline: "2px solid var(--green)", outlineOffset: -1 } : {}) }}>
                <div style={{ position: "relative", aspectRatio: "16/9", background: "var(--surface)", display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden" }}>
                  <label
                    style={{ position: "absolute", left: 6, top: 6, zIndex: 2, display: "flex", cursor: "pointer", background: "var(--bg)", borderRadius: 4, padding: 3, lineHeight: 0 }}
                    title="选择此镜头（用于批量出图）"
                  >
                    <input type="checkbox" checked={picked} onChange={() => sel.toggle(s.id)} disabled={batch.running} />
                  </label>
                  {s.image_url ? (
                    <img src={s.image_url} alt={s.title ?? ""} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                  ) : (
                    <ImageIcon size={24} color="var(--text3)" />
                  )}
                  <span style={{ position: "absolute", right: 6, top: 6, fontSize: 11, padding: "1px 6px", borderRadius: 4, background: "var(--bg)", color: "var(--text2)" }}>
                    镜头 {String(s.storyboard_number).padStart(2, "0")}
                  </span>
                  {imaging === s.id && (
                    <span style={{ position: "absolute", right: 6, bottom: 6, zIndex: 2, color: "var(--green-t)" }}><Loader2 size={16} className="spin" /></span>
                  )}
                </div>
                <AssetHistoryStrip
                  targetType="storyboard"
                  targetId={s.id}
                  currentImageUrl={s.image_url}
                  onUse={(imageUrl) => setShots((previous) => previous.map((item) => item.id === s.id ? { ...item, image_url: imageUrl } : item))}
                />
                <div style={{ padding: "8px 10px", display: "flex", flexDirection: "column", gap: 5, flex: 1 }}>
                  <div style={{ fontSize: 12, fontWeight: 500 }}>{s.title || `镜头 ${s.storyboard_number}`}</div>
                  <div style={{ fontSize: 11, color: "var(--text3)" }}>
                    {[s.shot_type, s.location, s.duration ? `${s.duration}s` : null].filter(Boolean).join(" · ")}
                  </div>
                  {s.action && <div style={{ fontSize: 11, color: "var(--text2)", lineHeight: 1.4 }}>{s.action}</div>}
                  <label style={{ fontSize: 10.5, color: "var(--text3)", marginTop: 2 }}>画面提示词（可编辑）</label>
                  <textarea
                    value={prompts[s.id] ?? ""}
                    onChange={(e) => setPrompts((p) => ({ ...p, [s.id]: e.target.value }))}
                    rows={3}
                    style={{ resize: "vertical", fontSize: 11, lineHeight: 1.45 }}
                    placeholder="这个镜头的画面提示词…"
                  />
                  <AssetGenerationControls
                    resolution={resolutions[s.id] ?? "landscape_1024x576"}
                    nodeId={nodeIds[s.id]}
                    nodes={nodes}
                    disabled={batch.running}
                    onResolutionChange={(value) => setResolutions((previous) => ({ ...previous, [s.id]: value }))}
                    onNodeChange={(value) => setNodeIds((previous) => ({ ...previous, [s.id]: value }))}
                  />
                  <div style={{ marginTop: "auto", display: "flex", gap: 6 }}>
                    <button
                      className="btn-secondary"
                      style={{ flex: "none", padding: "5px 9px", opacity: saving === s.id ? 0.7 : 1 }}
                      onClick={() => savePrompt(s)}
                      disabled={saving === s.id || batch.running}
                      title="只保存提示词，不出图"
                    >
                      {saving === s.id ? <Loader2 size={12} className="spin" /> : <Save size={12} />}
                    </button>
                    <button
                      className="btn-secondary"
                      style={{ flex: 1, padding: "5px 10px", opacity: imaging === s.id ? 0.7 : 1 }}
                      onClick={() => makeImage(s)}
                      disabled={imaging === s.id || batch.running}
                    >
                      {imaging === s.id ? <Loader2 size={13} className="spin" /> : <ImageIcon size={13} />} {s.image_url ? "重新出图" : "出图"}
                    </button>
                  </div>
                </div>
              </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
