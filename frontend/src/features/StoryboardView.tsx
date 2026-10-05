import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Edit3,
  Focus,
  Image as ImageIcon,
  Layers,
  LayoutGrid,
  Loader2,
  Minus,
  Plus,
  RotateCcw,
  Save,
  Sparkles,
  Wand2,
  X,
  ZoomIn,
} from "lucide-react";
import {
  batchGenerateStoryboardPrompts,
  generateStoryboards,
  listStoryboards,
  mediaDisplayUrl,
  splitStoryboard,
  updateStoryboard,
  type ComplianceHit,
  type EpisodeSummary,
  type Project,
  type Storyboard,
  type StoryboardUpdate,
} from "../api/client";
import AssetHistoryStrip from "../components/AssetHistoryStrip";
import ImageLightbox from "../components/ImageLightbox";
import MediaUploadButton from "../components/MediaUploadButton";
import {
  InfiniteCanvas,
  type CanvasPoint,
  type InfiniteCanvasHandle,
  useCanvasNodeDrag,
} from "../components/InfiniteCanvas";
import { useSelection } from "../components/useSelection";

const NODE_W = 240;
const NODE_H = 268;
const COL_GAP = 48;
const ROW_GAP = 36;
const COLS = 4;

/** 与 ComfyUI 单镜上限一致：分镜工作台时长 1–5 秒 */
const SB_MAX_DURATION = 5;
const SB_DEFAULT_DURATION = 5;

function clampShotDuration(value: number | null | undefined): number {
  const n = Math.round(Number(value));
  if (!Number.isFinite(n) || n <= 0) return SB_DEFAULT_DURATION;
  return Math.min(Math.max(n, 1), SB_MAX_DURATION);
}

function normalizeShot(sb: Storyboard): Storyboard {
  return { ...sb, duration: clampShotDuration(sb.duration) };
}

/** 一律按镜号排序，保证画布 / 胶片条 / 检视号一致 */
function sortShots(rows: Storyboard[]): Storyboard[] {
  return [...rows].sort((a, b) => a.storyboard_number - b.storyboard_number || a.id - b.id);
}

function shotNumLabel(n: number): string {
  return `#${String(n).padStart(2, "0")}`;
}

/** 运镜段落色条（按 segment_key 稳定哈希） */
function segmentColor(key: string | null | undefined): string {
  if (!key) return "transparent";
  let hash = 0;
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  const hue = hash % 360;
  return `hsl(${hue} 62% 48%)`;
}

function segmentBadge(shot: Storyboard): string | null {
  if (!shot.in_segment || !shot.segment_part || !shot.segment_total) return null;
  const title = shot.segment_title ? `${shot.segment_title} ` : "";
  return `${title}${shot.segment_part}/${shot.segment_total}`;
}

type ViewMode = "canvas" | "grid";
type Positions = Record<number, CanvasPoint>;

function posKey(episodeId: number) {
  return `xigua-sb-canvas-pos:${episodeId}`;
}

function loadPositions(episodeId: number): Positions {
  try {
    const raw = localStorage.getItem(posKey(episodeId));
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Positions;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function savePositions(episodeId: number, positions: Positions) {
  try {
    localStorage.setItem(posKey(episodeId), JSON.stringify(positions));
  } catch {
    /* ignore quota */
  }
}

function autoLayout(shots: Storyboard[]): Positions {
  const next: Positions = {};
  // 必须按镜号排位置，否则画布从左到右会和 #01 #02… 对不上
  sortShots(shots).forEach((s, i) => {
    const col = i % COLS;
    const row = Math.floor(i / COLS);
    next[s.id] = {
      x: col * (NODE_W + COL_GAP),
      y: row * (NODE_H + ROW_GAP),
    };
  });
  return next;
}

function boundsOf(positions: Positions, ids: number[]) {
  if (!ids.length) return undefined;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const id of ids) {
    const p = positions[id];
    if (!p) continue;
    minX = Math.min(minX, p.x);
    minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x + NODE_W);
    maxY = Math.max(maxY, p.y + NODE_H);
  }
  if (!Number.isFinite(minX)) return undefined;
  return { minX, minY, maxX, maxY };
}

function ShotNode({
  shot,
  pos,
  selected,
  picked,
  imaging,
  zoomRef,
  onSelect,
  onTogglePick,
  onMove,
  onMoveEnd,
  onPreview,
}: {
  shot: Storyboard;
  pos: CanvasPoint;
  selected: boolean;
  picked: boolean;
  imaging: boolean;
  zoomRef: React.RefObject<number>;
  onSelect: () => void;
  onTogglePick: () => void;
  onMove: (id: number, next: CanvasPoint) => void;
  onMoveEnd: (id: number, next: CanvasPoint) => void;
  onPreview: (url: string) => void;
}) {
  const drag = useCanvasNodeDrag(
    shot.id,
    pos,
    zoomRef,
    (id, next) => onMove(id as number, next),
    (id, next) => onMoveEnd(id as number, next),
  );

  const badge = segmentBadge(shot);
  const segHue = segmentColor(shot.segment_key);

  return (
    <div
      className={`sb-node${selected ? " selected" : ""}${picked ? " picked" : ""}${shot.in_segment ? " in-segment" : ""}`}
      style={{
        left: pos.x,
        top: pos.y,
        // 选中/多选时抬高层级，避免拆镜后节点重叠时点不到
        zIndex: selected || picked ? 4 : 1,
        boxShadow: shot.in_segment ? `inset 3px 0 0 ${segHue}` : undefined,
      }}
      onPointerDown={drag.onPointerDown}
      onClick={(e) => {
        e.stopPropagation();
        // Shift+点击：批量勾选；普通点击：进入检视
        if (e.shiftKey) {
          onTogglePick();
          return;
        }
        onSelect();
      }}
    >
      <div className="sb-node-head">
        <label
          data-no-drag
          title="批量选择"
          style={{ display: "flex", cursor: "pointer", margin: 0, padding: 2 }}
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
        >
          <input
            type="checkbox"
            checked={picked}
            onChange={(e) => {
              e.stopPropagation();
              onTogglePick();
            }}
            onClick={(e) => e.stopPropagation()}
          />
        </label>
        <strong title={shot.title ?? undefined}>{shot.title || `镜头 ${shot.storyboard_number}`}</strong>
        <span className="sb-node-badge" title={`数据库 id=${shot.id}`}>
          {shotNumLabel(shot.storyboard_number)}
        </span>
      </div>
      {badge && (
        <div
          data-no-drag
          title={shot.segment_title || "运镜段落"}
          style={{
            margin: "0 8px 4px",
            padding: "1px 6px",
            borderRadius: 4,
            fontSize: 10,
            color: "#fff",
            background: segHue,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            pointerEvents: "none",
          }}
        >
          段落 {badge}
        </div>
      )}
      <div className="sb-node-preview">
        {shot.image_url ? (
          <img
            src={mediaDisplayUrl(shot.image_url)}
            alt=""
            data-no-drag
            title="单击选中 · 双击放大"
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
            onClick={(e) => {
              // 必须选中镜头；原先 stopPropagation + 仅预览会导致「有图点不上」
              e.stopPropagation();
              if (e.shiftKey) {
                onTogglePick();
                return;
              }
              onSelect();
            }}
            onDoubleClick={(e) => {
              e.stopPropagation();
              onPreview(mediaDisplayUrl(shot.image_url));
            }}
          />
        ) : (
          <ImageIcon size={22} color="var(--text3)" />
        )}
        {shot.image_url && (
          <button
            type="button"
            data-no-drag
            title="放大预览"
            className="sb-node-preview-zoom"
            onClick={(e) => {
              e.stopPropagation();
              onPreview(shot.image_url!);
            }}
            onPointerDown={(e) => e.stopPropagation()}
          >
            <ZoomIn size={12} />
          </button>
        )}
        {imaging && (
          <span style={{ position: "absolute", right: 6, bottom: 6, color: "var(--green-t)" }}>
            <Loader2 size={16} className="spin" />
          </span>
        )}
      </div>
      <div className="sb-node-body">
        <div className="sb-node-meta">
          {[shot.shot_type, shot.location, `${clampShotDuration(shot.duration)}s`].filter(Boolean).join(" · ")}
        </div>
        <div className="sb-node-action">{shot.action || shot.dialogue || "暂无动作描述"}</div>
        <div className={`sb-node-status${shot.dialogue || shot.image_url || shot.video_url ? " done" : ""}`}>
          {shot.video_url
            ? "已有成片"
            : shot.dialogue
              ? "台词已写（出片用）"
              : shot.image_url
                ? "有参考图"
                : "待补提示词/台词"}
        </div>
      </div>
    </div>
  );
}

function EdgeSvg({ from, to, solid }: { from: CanvasPoint; to: CanvasPoint; solid?: boolean }) {
  const x1 = from.x + NODE_W;
  const y1 = from.y + NODE_H / 2;
  const x2 = to.x;
  const y2 = to.y + NODE_H / 2;
  const mx = (x1 + x2) / 2;
  const d = `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
  const minX = Math.min(x1, x2) - 4;
  const minY = Math.min(y1, y2) - 4;
  const w = Math.abs(x2 - x1) + 8;
  const h = Math.abs(y2 - y1) + 8;
  const stroke = solid ? "rgba(72, 201, 176, 0.75)" : "rgba(91,141,239,0.35)";
  return (
    <svg
      className="sb-edge"
      width={Math.max(w, 8)}
      height={Math.max(h, 8)}
      style={{ left: minX, top: minY }}
      viewBox={`${minX} ${minY} ${Math.max(w, 8)} ${Math.max(h, 8)}`}
    >
      <path d={d} fill="none" stroke={stroke} strokeWidth={solid ? 2.5 : 2} strokeDasharray={solid ? undefined : "6 4"} />
      <circle cx={x2} cy={y2} r={3.5} fill={solid ? "rgba(72, 201, 176, 0.9)" : "rgba(91,141,239,0.7)"} />
    </svg>
  );
}

export default function StoryboardView({
  current,
  username,
  onBanned,
  onGoto,
}: {
  current: { drama: Project; episode: EpisodeSummary } | null;
  username: string;
  onBanned?: () => void;
  onGoto?: (view: string) => void;
}) {
  const episode = current?.episode ?? null;
  const [shots, setShots] = useState<Storyboard[]>([]);
  const [loading, setLoading] = useState(false);
  const [breaking, setBreaking] = useState(false);
  const [splitting, setSplitting] = useState(false);
  const [saving, setSaving] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [editDraft, setEditDraft] = useState<StoryboardUpdate>({});
  const [polishing, setPolishing] = useState(false);
  const [prompts, setPrompts] = useState<Record<number, string>>({});
  const [dialogues, setDialogues] = useState<Record<number, string>>({});
  const [err, setErr] = useState<{ message?: string; hits?: ComplianceHit[]; level?: string } | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [mode, setMode] = useState<ViewMode>("canvas");
  const [positions, setPositions] = useState<Positions>({});
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [zoomLabel, setZoomLabel] = useState(85);
  const canvasRef = useRef<InfiniteCanvasHandle>(null);
  const zoomRef = useRef(0.85);
  const sel = useSelection();
  const fittedRef = useRef<number | null>(null);
  const stripRef = useRef<HTMLDivElement>(null);

  const selected = useMemo(
    () => (selectedId != null ? shots.find((s) => s.id === selectedId) ?? null : null),
    [selectedId, shots],
  );

  const focusShot = (id: number) => {
    setSelectedId(id);
    setEditing(false);
    if (!sel.selected.has(id) && (sel.selected.size === 0 || sel.selected.size === 1)) {
      sel.replace([id]);
    }
    const p = positions[id];
    if (p && mode === "canvas" && canvasRef.current?.ensureVisible) {
      canvasRef.current.ensureVisible(
        { minX: p.x, minY: p.y, maxX: p.x + NODE_W, maxY: p.y + NODE_H },
        40,
      );
    }
  };

  useEffect(() => {
    if (selectedId == null || !stripRef.current) return;
    const parent = stripRef.current;
    const el = parent.querySelector(`[data-shot="${selectedId}"]`) as HTMLElement | null;
    if (!el) return;
    const pRect = parent.getBoundingClientRect();
    const eRect = el.getBoundingClientRect();
    if (eRect.left < pRect.left + 8 || eRect.right > pRect.right - 8) {
      el.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    }
  }, [selectedId]);

  const seedPrompts = (rows: Storyboard[]) => {
    setPrompts(() => {
      const next: Record<number, string> = {};
      rows.forEach((s) => {
        next[s.id] = s.image_prompt ?? "";
      });
      return next;
    });
    setDialogues(() => {
      const next: Record<number, string> = {};
      rows.forEach((s) => {
        next[s.id] = s.dialogue ?? "";
      });
      return next;
    });
  };

  const applyLayout = useCallback((rows: Storyboard[], episodeId: number, force = false) => {
    const orderedRows = sortShots(rows);
    const saved = force ? {} : loadPositions(episodeId);
    const layout = autoLayout(orderedRows);
    const merged: Positions = {};
    orderedRows.forEach((s) => {
      merged[s.id] = saved[s.id] ?? layout[s.id];
    });
    setPositions(merged);
    if (force || !Object.keys(saved).length) savePositions(episodeId, merged);
    return merged;
  }, []);

  const load = async (epId: number) => {
    setLoading(true);
    try {
      const rows = sortShots((await listStoryboards(epId)).map(normalizeShot));
      setShots(rows);
      seedPrompts(rows);
      // 有新镜（拆镜后）缺坐标时强制按镜号重排；否则保留用户拖过的位置
      const saved = loadPositions(epId);
      const needRelayout = rows.some((r) => !saved[r.id]);
      applyLayout(rows, epId, needRelayout);
      if (rows.length && (selectedId == null || !rows.some((r) => r.id === selectedId))) {
        setSelectedId(rows[0].id);
      }
      if (!rows.length) setSelectedId(null);
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
      // 同时保存画面提示词 + 台词（台词会进视频模型提示词生成语音）
      const updated = normalizeShot(
        await updateStoryboard(sb.id, {
          image_prompt: prompts[sb.id] ?? "",
          dialogue: dialogues[sb.id] ?? sb.dialogue ?? "",
        }),
      );
      setShots((previous) => previous.map((item) => (item.id === sb.id ? { ...item, ...updated } : item)));
      setPrompts((previous) => ({ ...previous, [sb.id]: updated.image_prompt ?? prompts[sb.id] ?? "" }));
      setDialogues((previous) => ({ ...previous, [sb.id]: updated.dialogue ?? dialogues[sb.id] ?? "" }));
    } catch {
      setErr({ message: "保存提示词/台词失败" });
    } finally {
      setSaving(null);
    }
  };

  const openEditor = (sb: Storyboard) => {
    setEditing(true);
    setEditDraft({
      title: sb.title ?? "",
      location: sb.location ?? "",
      time: sb.time ?? "",
      shot_type: sb.shot_type ?? "",
      angle: sb.angle ?? "",
      movement: sb.movement ?? "",
      action: sb.action ?? "",
      dialogue: sb.dialogue ?? "",
      sound_effect: sb.sound_effect ?? "",
      duration: clampShotDuration(sb.duration),
      image_prompt: prompts[sb.id] ?? sb.image_prompt ?? "",
      video_prompt: sb.video_prompt ?? "",
    });
  };

  const saveEdits = async (sb: Storyboard) => {
    setSaving(sb.id);
    setErr(null);
    try {
      const payload: StoryboardUpdate = {
        ...editDraft,
        duration: clampShotDuration(editDraft.duration),
      };
      const updated = normalizeShot(await updateStoryboard(sb.id, payload));
      setShots((previous) => previous.map((item) => (item.id === sb.id ? updated : item)));
      setPrompts((previous) => ({ ...previous, [sb.id]: updated.image_prompt ?? "" }));
      setDialogues((previous) => ({ ...previous, [sb.id]: updated.dialogue ?? "" }));
      setEditing(false);
      setEditDraft({});
    } catch (reason) {
      setErr({ message: reason instanceof Error ? reason.message : "保存分镜修改失败" });
    } finally {
      setSaving(null);
    }
  };

  const saveReferenceOverride = async (sb: Storyboard, urls: string[] | null) => {
    setSaving(sb.id);
    setErr(null);
    try {
      const updated = await updateStoryboard(sb.id, { reference_images: urls });
      setShots((previous) => previous.map((item) => (item.id === sb.id ? updated : item)));
    } catch (reason) {
      setErr({ message: reason instanceof Error ? reason.message : "保存参考图设置失败" });
    } finally {
      setSaving(null);
    }
  };

  useEffect(() => {
    if (episode) {
      fittedRef.current = null;
      load(episode.id);
    } else {
      setShots([]);
      setPositions({});
      setSelectedId(null);
    }
    setErr(null);
    setEditing(false);
    sel.clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [episode?.id]);

  useEffect(() => {
    if (mode !== "canvas" || !episode || !shots.length || loading) return;
    if (fittedRef.current === episode.id) return;
    const b = boundsOf(positions, shots.map((s) => s.id));
    if (!b) return;
    const t = window.setTimeout(() => {
      canvasRef.current?.fitView(b, 72);
      fittedRef.current = episode.id;
    }, 40);
    return () => clearTimeout(t);
  }, [mode, episode, shots, positions, loading]);

  const breakdown = async () => {
    if (!episode) return;
    setBreaking(true);
    setErr(null);
    try {
      const res = await generateStoryboards(episode.id, username);
      if (res.status === 200 && res.storyboards) {
        const nextShots = sortShots(res.storyboards.map(normalizeShot));
        setShots(nextShots);
        seedPrompts(nextShots);
        sel.clear();
        const layout = applyLayout(nextShots, episode.id, true);
        setSelectedId(nextShots[0]?.id ?? null);
        fittedRef.current = null;
        window.setTimeout(() => {
          canvasRef.current?.fitView(boundsOf(layout, nextShots.map((s) => s.id)), 72);
          fittedRef.current = episode.id;
        }, 60);
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
          shots.forEach((s) => {
            byNumber.set(s.storyboard_number, s);
          });
          res.prompts.forEach((p) => {
            const sb = [...byNumber.values()].find((s) => s.storyboard_number === p.number);
            if (sb) next[sb.id] = p.prompt;
          });
          return next;
        });
      }
    } catch (e: unknown) {
      setErr({ message: e instanceof Error ? e.message : "润色提示词失败" });
    } finally {
      setPolishing(false);
    }
  };

  /** 同一运镜段落的全部镜头（按镜号） */
  const segmentShots = (key: string | null | undefined) =>
    key ? sortShots(shots.filter((s) => s.segment_key === key && s.in_segment)) : [];

  const selectSegment = (key: string | null | undefined) => {
    const ids = segmentShots(key).map((s) => s.id);
    if (ids.length) sel.replace(ids);
  };

  const onNodeMove = (id: number, next: CanvasPoint) => {
    setPositions((prev) => ({ ...prev, [id]: next }));
  };

  const onNodeMoveEnd = (id: number, next: CanvasPoint) => {
    if (!episode) return;
    setPositions((prev) => {
      const merged = { ...prev, [id]: next };
      savePositions(episode.id, merged);
      return merged;
    });
  };

  const relayout = () => {
    if (!episode) return;
    const layout = applyLayout(shots, episode.id, true);
    fittedRef.current = null;
    window.setTimeout(() => {
      canvasRef.current?.fitView(boundsOf(layout, shots.map((s) => s.id)), 72);
      fittedRef.current = episode.id;
    }, 40);
  };

  if (!episode) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text3)", fontSize: 13 }}>
        请先在「项目」里选择一个分集，再拆镜头脚本（多图参考出片，不做分镜静帧图）。
      </div>
    );
  }

  const allIds = shots.map((s) => s.id);

  const ordered = sortShots(shots);
  const edges: { from: number; to: number; solid: boolean }[] = [];
  for (let i = 0; i < ordered.length - 1; i++) {
    const a = ordered[i];
    const b = ordered[i + 1];
    const solid = Boolean(a.segment_key && a.segment_key === b.segment_key && a.in_segment);
    edges.push({ from: a.id, to: b.id, solid });
  }

  const runSplit = async (sb: Storyboard, parts?: number) => {
    setSplitting(true);
    setErr(null);
    try {
      const result = await splitStoryboard(sb.id, { parts, use_llm: true, username });
      // 优先用接口返回的全表；若缺则回拉，避免拆镜后 id/布局错乱导致点选失效
      let next = sortShots((result.storyboards || []).map(normalizeShot));
      if (!next.length && episode) {
        next = sortShots((await listStoryboards(episode.id)).map(normalizeShot));
      }
      setShots(next);
      seedPrompts(next);
      sel.clear();
      // 拆出的子镜默认勾选，方便批量润色提示词
      if (result.storyboard_ids?.length) sel.replace(result.storyboard_ids);
      const layout = episode ? applyLayout(next, episode.id, true) : {};
      const focusId = result.storyboard_ids?.[0] ?? sb.id;
      setSelectedId(focusId);
      setEditing(false);
      fittedRef.current = null;
      const n = result.parts || result.storyboard_ids?.length || 0;
      const nums = (result.storyboard_numbers || [])
        .map((num) => shotNumLabel(num))
        .join("、");
      const ttsOk = result.tts_ok;
      const ttsFail = result.tts_fail;
      const ttsHint =
        typeof ttsOk === "number" && ttsOk > 0
          ? ttsFail
            ? `台词已按子镜拆分，配音成功 ${ttsOk}、失败 ${ttsFail}（失败请到时间线重配）。`
            : `台词已按子镜拆分；定稿出片时 H3 会按「角色名：台词」生成语音口型。`
          : result.needs_tts
            ? "旧整段配音已清空；请到时间线为有台词子镜生成配音后再定稿出片对口型。"
            : "";
      setErr({
        message:
          `已拆成 ${n} 镜：${nums || "见画布"}。请补提示词/台词，到成片台用角色·场景多图参考出视频。${ttsHint}画布已按镜号重排。`,
        level: "yellow",
      });
      window.setTimeout(() => {
        if (!episode) return;
        const ids = result.storyboard_ids?.length ? result.storyboard_ids : next.map((s) => s.id);
        canvasRef.current?.fitView(boundsOf(layout, ids), 72);
        fittedRef.current = episode.id;
      }, 50);
    } catch (reason) {
      setErr({ message: reason instanceof Error ? reason.message : "长镜拆解失败" });
    } finally {
      setSplitting(false);
    }
  };

  const renderInspector = () => {
    if (!selected) {
      return (
        <div className="inspector-empty">
          <Layers size={28} />
          <div>在画布上点击分镜节点</div>
          <div style={{ fontSize: 11 }}>编辑画面/视频提示词与「角色：台词」；出片在成片台用多参考图</div>
        </div>
      );
    }
    const s = selected;
    return (
      <>
        <h3>
          <span className="sb-node-badge" title={`内部 id=${s.id}（显示按镜号）`}>
            {shotNumLabel(s.storyboard_number)}
          </span>
          {s.title || `镜头 ${s.storyboard_number}`}
          <button
            className="storyboard-edit-toggle"
            style={{ marginLeft: "auto" }}
            onClick={() => (editing ? setEditing(false) : openEditor(s))}
            title="手动修改完整分镜"
          >
            {editing ? <X size={12} /> : <Edit3 size={12} />}
          </button>
        </h3>
        <div style={{ fontSize: 10, color: "var(--text3)", marginTop: -4 }}>
          全片镜号 {shotNumLabel(s.storyboard_number)}
          {s.in_segment && s.segment_part && s.segment_total
            ? ` · 段落 ${s.segment_part}/${s.segment_total}`
            : ""}
        </div>
        {s.in_segment && s.segment_key && (
          <div
            style={{
              marginBottom: 8,
              padding: "8px 10px",
              borderRadius: 8,
              border: `1px solid ${segmentColor(s.segment_key)}`,
              background: "var(--panel2)",
              fontSize: 11,
              color: "var(--text2)",
            }}
          >
            <div style={{ marginBottom: 6 }}>
              <strong style={{ color: segmentColor(s.segment_key) }}>运镜段落</strong>
              {" · "}
              {s.segment_title || "连续镜头"}
              {" · "}
              {s.segment_part}/{s.segment_total}
              <span style={{ color: "var(--text3)" }}>
                {" "}
                （共 {segmentShots(s.segment_key).length} 镜 · 出片走成片台多参考）
              </span>
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <button
                type="button"
                className="btn-secondary"
                style={{ width: "auto", padding: "5px 10px", fontSize: 11 }}
                disabled={false || splitting}
                title="勾选本段落全部子镜（如 1/5～5/5）"
                onClick={() => selectSegment(s.segment_key)}
              >
                选本段落
              </button>
              {onGoto ? (
                <button
                  type="button"
                  className="btn-primary"
                  style={{ width: "auto", padding: "5px 10px", fontSize: 11 }}
                  title="到成片台对本段批量出视频（多参考 r2v）"
                  onClick={() => onGoto("timeline")}
                >
                  <Sparkles size={12} /> 成片台出本段视频
                </button>
              ) : null}
            </div>
            <p style={{ margin: "6px 0 0", fontSize: 10, color: "var(--text3)", lineHeight: 1.4 }}>
              本段完善提示词与台词后，到「成片」台用角色/场景多参考图出视频（语音在提示词中）。
            </p>
          </div>
        )}
        <div style={{ display: "flex", gap: 6, marginBottom: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn-secondary"
            style={{ width: "auto", padding: "4px 8px", fontSize: 11 }}
            disabled={splitting || false}
            title="把本镜拆成 2–4 个连续 3–5 秒子镜，保持场景人物一致，便于完整运镜"
            onClick={() => runSplit(s)}
          >
            {splitting ? <Loader2 size={12} className="spin" /> : <Layers size={12} />}
            {" "}长镜拆解
          </button>
          <button
            type="button"
            className="btn-secondary"
            style={{ width: "auto", padding: "4px 8px", fontSize: 11 }}
            disabled={splitting || false}
            title="固定拆成 3 段（起幅/中段/落幅）"
            onClick={() => runSplit(s, 3)}
          >
            拆 3 镜
          </button>
        </div>
        <p style={{ margin: "0 0 8px", fontSize: 10.5, color: "var(--text3)", lineHeight: 1.45 }}>
          ComfyUI 单镜最长 5s。拆镜后用上方「选本段落 / 出本段全部图」出齐；成片台再出视频。
        </p>

        <div className="sb-node-preview" style={{ borderRadius: 8, border: "1px solid var(--border)", position: "relative" }}>
          {s.image_url ? (
            <img
              src={mediaDisplayUrl(s.image_url)}
              alt=""
              onClick={() => setPreview(mediaDisplayUrl(s.image_url))}
              style={{ cursor: "zoom-in", width: "100%", height: "100%", objectFit: "cover" }}
            />
          ) : (
            <ImageIcon size={24} color="var(--text3)" />
          )}
          {false && (
            <span style={{ position: "absolute", right: 8, bottom: 8, color: "var(--green-t)" }}>
              <Loader2 size={16} className="spin" />
            </span>
          )}
        </div>

        <AssetHistoryStrip
          targetType="storyboard"
          targetId={s.id}
          currentImageUrl={s.image_url}
          onUse={(imageUrl) =>
            setShots((previous) => previous.map((item) => (item.id === s.id ? { ...item, image_url: imageUrl } : item)))
          }
        />

        <div style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: s.reference_images.length ? 5 : 0 }}>
            <span style={{ fontSize: 10.5, color: "var(--text3)", flex: 1 }}>
              {s.reference_mode === "auto" ? "智能参考（本软件人物/场景/道具）" : "手动锁定"} · {s.reference_images.length} 张
            </span>
            {s.reference_mode === "manual" && (
              <button
                className="storyboard-edit-toggle"
                title="恢复按剧情自动选择参考图"
                disabled={saving === s.id || false}
                onClick={() => saveReferenceOverride(s, null)}
              >
                <RotateCcw size={11} />
              </button>
            )}
          </div>
          {s.reference_images.length > 0 && (
            <div style={{ display: "flex", gap: 5, overflowX: "auto" }}>
              {s.reference_images.map((reference, index) => (
                <div
                  key={`${reference.url}-${index}`}
                  style={{
                    position: "relative",
                    flex: "0 0 52px",
                    outline: reference.is_speaker ? "1px solid var(--green)" : undefined,
                    borderRadius: 6,
                  }}
                  title={reference.label}
                >
                  <img
                    src={reference.preview_url}
                    alt={reference.label}
                    onClick={() => setPreview(reference.preview_url)}
                    style={{
                      width: 48,
                      height: 36,
                      objectFit: "cover",
                      borderRadius: 4,
                      border: "1px solid var(--border)",
                      cursor: "zoom-in",
                    }}
                  />
                  <button
                    title={`不再引用：${reference.label}`}
                    disabled={saving === s.id || false}
                    onClick={() =>
                      saveReferenceOverride(
                        s,
                        s.reference_images.filter((_, i) => i !== index).map((item) => item.url),
                      )
                    }
                    style={{
                      position: "absolute",
                      right: -3,
                      top: -3,
                      width: 14,
                      height: 14,
                      borderRadius: 7,
                      border: 0,
                      padding: 0,
                      background: "var(--bg)",
                      color: "var(--text2)",
                      cursor: "pointer",
                      display: "grid",
                      placeItems: "center",
                    }}
                  >
                    <X size={9} />
                  </button>
                  <div
                    style={{
                      fontSize: 9,
                      color: reference.is_speaker ? "var(--green-t)" : "var(--text3)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {reference.is_speaker
                      ? `@说:${reference.asset_name || ""}`
                      : reference.kind === "character"
                        ? `@${reference.asset_name || "角"}`
                        : reference.kind === "scene"
                          ? "@场景"
                          : reference.kind === "prop"
                            ? `@道:${reference.asset_name || ""}`
                            : reference.label}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {editing ? (
          <div className="storyboard-edit-panel">
            <div className="storyboard-edit-grid">
              <label>
                标题
                <input value={editDraft.title ?? ""} onChange={(e) => setEditDraft((p) => ({ ...p, title: e.target.value }))} />
              </label>
              <label>
                时长（秒，AI 最长 5s）
                <input
                  type="number"
                  min={1}
                  max={5}
                  value={editDraft.duration ?? 5}
                  onChange={(e) =>
                    setEditDraft((p) => ({
                      ...p,
                      duration: Math.min(Math.max(Number(e.target.value) || 1, 1), 5),
                    }))
                  }
                />
              </label>
              <label>
                场景
                <input value={editDraft.location ?? ""} onChange={(e) => setEditDraft((p) => ({ ...p, location: e.target.value }))} />
              </label>
              <label>
                时间
                <input value={editDraft.time ?? ""} onChange={(e) => setEditDraft((p) => ({ ...p, time: e.target.value }))} />
              </label>
              <label>
                景别
                <input value={editDraft.shot_type ?? ""} onChange={(e) => setEditDraft((p) => ({ ...p, shot_type: e.target.value }))} />
              </label>
              <label>
                角度
                <input value={editDraft.angle ?? ""} onChange={(e) => setEditDraft((p) => ({ ...p, angle: e.target.value }))} />
              </label>
              <label>
                运镜
                <input value={editDraft.movement ?? ""} onChange={(e) => setEditDraft((p) => ({ ...p, movement: e.target.value }))} />
              </label>
              <label>
                音效
                <input
                  value={editDraft.sound_effect ?? ""}
                  onChange={(e) => setEditDraft((p) => ({ ...p, sound_effect: e.target.value }))}
                />
              </label>
            </div>
            <label>
              动作
              <textarea rows={2} value={editDraft.action ?? ""} onChange={(e) => setEditDraft((p) => ({ ...p, action: e.target.value }))} />
            </label>
            <label>
              台词
              <textarea rows={2} value={editDraft.dialogue ?? ""} onChange={(e) => setEditDraft((p) => ({ ...p, dialogue: e.target.value }))} />
            </label>
            <label>
              画面提示词
              <textarea
                rows={3}
                value={editDraft.image_prompt ?? ""}
                onChange={(e) => setEditDraft((p) => ({ ...p, image_prompt: e.target.value }))}
              />
            </label>
            <label>
              视频提示词
              <textarea
                rows={3}
                value={editDraft.video_prompt ?? ""}
                onChange={(e) => setEditDraft((p) => ({ ...p, video_prompt: e.target.value }))}
              />
            </label>
            <div className="storyboard-edit-actions">
              <button className="btn-secondary" onClick={() => { setEditing(false); setEditDraft({}); }}>
                <X size={12} /> 取消
              </button>
              <button className="btn-primary" onClick={() => saveEdits(s)} disabled={saving === s.id}>
                {saving === s.id ? <Loader2 size={12} className="spin" /> : <Save size={12} />} 保存修改
              </button>
            </div>
          </div>
        ) : (
          <>
            <div style={{ fontSize: 11, color: "var(--text2)", lineHeight: 1.5 }}>
              {[s.shot_type, s.angle, s.movement, s.location, s.time].filter(Boolean).join(" · ")}
            </div>
            {s.action && (
              <div style={{ fontSize: 11, color: "var(--text2)", lineHeight: 1.45 }}>
                <span style={{ color: "var(--text3)" }}>动作 · </span>
                {s.action}
                {(s.action.includes("字幕") || /text|subtitle|caption/i.test(s.action)) && (
                  <span style={{ display: "block", marginTop: 4, color: "var(--amber)", fontSize: 10 }}>
                    动作里含「字幕/文字」会污染视频提示词；点下方「去文字」或点铅笔改动作。
                  </span>
                )}
              </div>
            )}
            {s.dialogue && (
              <div style={{ fontSize: 11, color: "var(--amber)", lineHeight: 1.45, padding: "6px 8px", borderRadius: 6, background: "var(--surface)" }}>
                {s.dialogue}
              </div>
            )}
            <label style={{ gap: 6 }}>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                台词（出片时写入提示词生成语音）
                <span style={{ fontSize: 10, color: "var(--amber)", fontWeight: 400 }}>格式：角色名：说的话</span>
              </span>
              <textarea
                value={dialogues[s.id] ?? s.dialogue ?? ""}
                onChange={(e) => setDialogues((p) => ({ ...p, [s.id]: e.target.value }))}
                rows={3}
                placeholder={"例：\n夜苍溟：你终于来了。\n苏婉：……我只是路过。"}
                style={{ minHeight: 64, fontSize: 12, lineHeight: 1.5, borderColor: "var(--amber)" }}
              />
            </label>
            <label style={{ gap: 6 }}>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                画面/镜头提示词（成片主提示）
                <span style={{ fontSize: 10, color: "var(--text3)", fontWeight: 400 }}>保存后 → H3 编排优化 → 送模型出片</span>
              </span>
              <textarea
                value={prompts[s.id] ?? ""}
                onChange={(e) => setPrompts((p) => ({ ...p, [s.id]: e.target.value }))}
                rows={5}
                placeholder="描述本镜画面、运镜、气氛…保存后在成片台经 H3 编排优化后送 MiniMax 出视频"
                style={{ minHeight: 96, fontSize: 12, lineHeight: 1.5 }}
              />
            </label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              <button
                type="button"
                className="btn-secondary"
                style={{ width: "auto", padding: "3px 8px", fontSize: 10 }}
                title="从提示词去掉字幕/文字/水印等诱导词"
                disabled={false}
                onClick={() => {
                  const raw = prompts[s.id] ?? "";
                  const cleaned = raw
                    .replace(/字幕浮现|字幕出现|字幕升起|字幕|标题卡|花字|水印|大字报/gi, " ")
                    .replace(/\b(subtitle|captions?|title card|on-?screen text|with text|text overlay|chinese text)\b/gi, " ")
                    .replace(/\s{2,}/g, " ")
                    .replace(/[，,]{2,}/g, "，")
                    .trim();
                  setPrompts((p) => ({ ...p, [s.id]: cleaned }));
                }}
              >
                去文字
              </button>
              <button
                type="button"
                className="btn-secondary"
                style={{ width: "auto", padding: "3px 8px", fontSize: 10 }}
                title="追加无人空镜约束（适合落幅/环境镜）"
                disabled={false}
                onClick={() => {
                  const raw = (prompts[s.id] ?? "").trim();
                  const tag = "纯环境空镜，不要人物、士兵、行人、人体轮廓，不要任何文字字幕";
                  if (raw.includes("纯环境空镜")) return;
                  setPrompts((p) => ({ ...p, [s.id]: raw ? `${raw}，${tag}` : tag }));
                }}
              >
                无人空镜
              </button>
              <button
                type="button"
                className="btn-secondary"
                style={{ width: "auto", padding: "3px 8px", fontSize: 10 }}
                title="打开完整编辑（动作/台词/提示词）"
                disabled={false}
                onClick={() => openEditor(s)}
              >
                <Edit3 size={11} /> 改动作/台词
              </button>
            </div>
          </>
        )}

        {/* 分镜出图已取消：分辨率/算力在成片台出视频时选用 */}

        {!editing && (
          <div className="inspector-actions" style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            <button
              className="btn-secondary"
              style={{ opacity: saving === s.id ? 0.7 : 1, flex: "1 1 80px" }}
              onClick={() => savePrompt(s)}
              disabled={saving === s.id || false}
              title="保存画面提示词 + 台词（供成片模型生成语音）"
            >
              {saving === s.id ? <Loader2 size={13} className="spin" /> : <Save size={13} />} 保存提示词/台词
            </button>
            <MediaUploadButton
              targetType="storyboard_image"
              targetId={s.id}
              username={username}
              accept="image/png,image/jpeg,image/webp,image/gif"
              label=" 上传参考图"
              title="可选：上传本镜额外参考（成片会作为补充 <Picture>）"
              disabled={false}
              style={{ flex: "1 1 80px", padding: "6px 8px" }}
              onDone={async (r) => {
                const raw = r.image_url || r.url;
                setShots((prev) =>
                  prev.map((item) =>
                    item.id === s.id
                      ? { ...item, image_url: raw, first_frame_image: raw }
                      : item,
                  ),
                );
                if (episode?.id) await load(episode.id);
                setErr({ message: "参考图已上传（可选补充，不替代角色/场景定妆）" });
              }}
              onError={(m) => setErr({ message: m })}
            />
            <MediaUploadButton
              targetType="storyboard_video"
              targetId={s.id}
              username={username}
              accept="video/mp4,video/webm,video/quicktime"
              label=" 上传视频"
              title="从本机上传本镜成片视频"
              disabled={false}
              style={{ flex: "1 1 80px", padding: "6px 8px" }}
              onDone={async (r) => {
                setShots((prev) =>
                  prev.map((item) =>
                    item.id === s.id
                      ? {
                          ...item,
                          video_url: r.video_url || r.url,
                          image_url: r.image_url || item.image_url,
                        }
                      : item,
                  ),
                );
                if (episode?.id) await load(episode.id);
                setErr({ message: "分镜视频已上传并刷新" });
              }}
              onError={(m) => setErr({ message: m })}
            />
            {onGoto ? (
              <button
                type="button"
                className="btn-primary"
                style={{ flex: "1 1 100px" }}
                onClick={() => onGoto("timeline")}
                title="到成片台用角色/场景多参考图出视频"
              >
                <Sparkles size={13} /> 去成片出视频
              </button>
            ) : null}
          </div>
        )}
      </>
    );
  };

  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
        <Layers size={15} color="var(--text2)" />
        <span style={{ fontSize: 12, fontWeight: 500 }}>镜头脚本台</span>
        <span style={{ fontSize: 11, color: "var(--text3)" }}>
          {current?.drama.title} · 第{episode.episode_number}集 · {shots.length} 个镜头
        </span>
        <div className="storyboard-mode-toggle" style={{ marginLeft: 8 }}>
          <button type="button" className={mode === "canvas" ? "active" : ""} onClick={() => setMode("canvas")} title="无限画布">
            <Focus size={12} /> 画布
          </button>
          <button type="button" className={mode === "grid" ? "active" : ""} onClick={() => setMode("grid")} title="卡片网格">
            <LayoutGrid size={12} /> 网格
          </button>
        </div>
        <div style={{ flex: 1 }} />
        <button
          className="btn-primary"
          style={{ width: "auto", padding: "6px 14px", opacity: breaking ? 0.7 : 1 }}
          onClick={breakdown}
          disabled={breaking || false}
          title="用剧本拆成镜头列表（脚本/提示词/台词，不生成分镜静帧图）"
        >
          {breaking ? <Loader2 size={14} className="spin" /> : <Wand2 size={14} />}{" "}
          {shots.length ? "重拆镜头列表" : "AI 拆镜头列表"}
        </button>
      </div>
      {(() => {
        const weak = shots.filter((s) => {
          const r = s.readiness;
          if (!r) return false;
          return r.ref_count === 0 || (r.missing_cast_images && r.missing_cast_images.length > 0);
        });
        if (!weak.length) return null;
        const sample = weak[0];
        const miss = sample.readiness?.missing_cast_images?.slice(0, 3).join("、") || "";
        return (
          <div
            className="feature-notice"
            style={{ margin: "0 12px 8px", display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}
          >
            <AlertTriangle size={14} />
            <span>
              {weak.length} 镜缺少<strong>角色/场景定妆参考图</strong>
              {miss ? `（如：${miss}）` : ""}
              。成片是多图参考生视频，请先在资产台出齐参考图，再去成片台出片。
            </span>
            {onGoto ? (
              <>
                <button type="button" className="btn-secondary" style={{ width: "auto", padding: "2px 10px" }} onClick={() => onGoto("characters")}>
                  角色资产
                </button>
                <button type="button" className="btn-secondary" style={{ width: "auto", padding: "2px 10px" }} onClick={() => onGoto("scenes")}>
                  场景造景
                </button>
              </>
            ) : null}
          </div>
        );
      })()}

      {shots.length > 0 && (
        <div
          style={{
            padding: "8px 16px",
            borderBottom: "1px solid var(--border)",
            background: "var(--panel2)",
            fontSize: 12,
            color: "var(--text2)",
            lineHeight: 1.5,
          }}
        >
          <strong style={{ color: "var(--green-t)" }}>产品路径：多图参考生视频</strong>
          （不做「分镜静帧出图」）① AI 拆镜头列表 ② 写画面提示词 +「角色名：台词」
          ③（可选）Agent 审核脚本 ④ 角色/场景定妆图作参考 → 成片台出视频，语音在提示词里。
          {onGoto ? (
            <>
              <button
                type="button"
                className="btn-secondary"
                style={{ marginLeft: 10, width: "auto", padding: "4px 12px" }}
                onClick={() => onGoto("review")}
              >
                Agent 审核
              </button>
              <button
                type="button"
                className="btn-primary"
                style={{ marginLeft: 6, width: "auto", padding: "4px 12px" }}
                onClick={() => onGoto("timeline")}
              >
                多参考出片
              </button>
            </>
          ) : null}
        </div>
      )}

      {shots.length > 0 && sel.selected.size > 0 && !false && (
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
        <div
          style={{
            margin: "8px 12px 0",
            padding: "8px 10px",
            borderRadius: 6,
            border: `1px solid ${err.level === "red" ? "var(--red)" : "var(--amber)"}`,
            color: err.level === "red" ? "var(--red-t)" : "var(--amber)",
            fontSize: 12,
          }}
        >
          <div style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 500 }}>
            <AlertTriangle size={14} /> {err.level === "red" ? "红线拦截" : "提示"}
          </div>
          <div style={{ marginTop: 4, color: "var(--text2)" }}>{err.message}</div>
          {err.hits && err.hits.length > 0 && (
            <div style={{ marginTop: 4, color: "var(--text2)" }}>
              命中：{err.hits.map((h) => `${h.word}(${h.category})`).join("、")}
            </div>
          )}
        </div>
      )}

      {mode === "canvas" ? (
        <div className="storyboard-workbench">
          <div className="storyboard-workspace">
            <div className="storyboard-canvas-wrap">
              {loading ? (
                <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text3)", gap: 6 }}>
                  <Loader2 size={14} className="spin" /> 加载分镜…
                </div>
              ) : shots.length === 0 ? (
                <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "var(--text3)", gap: 8 }}>
                  <Layers size={32} />
                  <div style={{ fontSize: 13 }}>还没有分镜</div>
                  <div style={{ fontSize: 11 }}>点右上「AI 拆镜头列表」：剧本 → 镜头脚本（提示词/台词），成片用多图参考，不生成分镜静帧</div>
                  <div style={{ fontSize: 11, opacity: 0.7 }}>（需要该分集已生成剧本）</div>
                </div>
              ) : (
                <>
                  <InfiniteCanvas
                    ref={canvasRef}
                    onTransformChange={(t) => {
                      zoomRef.current = t.zoom;
                      setZoomLabel(Math.round(t.zoom * 100));
                    }}
                  >
                    {edges.map(({ from, to, solid }) => {
                      const a = positions[from];
                      const b = positions[to];
                      if (!a || !b) return null;
                      return <EdgeSvg key={`${from}-${to}`} from={a} to={b} solid={solid} />;
                    })}
                    {ordered.map((s) => {
                      const pos = positions[s.id] ?? { x: 0, y: 0 };
                      return (
                        <ShotNode
                          key={`sb-${s.id}-n${s.storyboard_number}`}
                          shot={s}
                          pos={pos}
                          selected={selectedId === s.id}
                          picked={sel.selected.has(s.id)}
                          imaging={false}
                          zoomRef={zoomRef}
                          onSelect={() => focusShot(s.id)}
                          onTogglePick={() => sel.toggle(s.id)}
                          onMove={onNodeMove}
                          onMoveEnd={onNodeMoveEnd}
                          onPreview={setPreview}
                        />
                      );
                    })}
                  </InfiniteCanvas>
                  <div className="storyboard-canvas-tools">
                    <button type="button" title="放大" onClick={() => canvasRef.current?.zoomBy(1.15)}>
                      <Plus size={14} />
                    </button>
                    <div className="zoom-label">{zoomLabel}%</div>
                    <button type="button" title="缩小" onClick={() => canvasRef.current?.zoomBy(1 / 1.15)}>
                      <Minus size={14} />
                    </button>
                    <button
                      type="button"
                      title="适应全部"
                      onClick={() => canvasRef.current?.fitView(boundsOf(positions, allIds), 72)}
                    >
                      <ZoomIn size={14} />
                    </button>
                    <button type="button" title="自动排布" onClick={relayout}>
                      <LayoutGrid size={14} />
                    </button>
                  </div>
                  <div className="storyboard-hint">滚轮缩放 · 空格/中键拖拽 · 点底部镜头条跳转</div>
                </>
              )}
            </div>
            <aside className="storyboard-inspector">{renderInspector()}</aside>
          </div>

          {shots.length > 0 && (
            <div className="timeline-filmstrip">
              <div className="filmstrip-toolbar">
                <label className="filmstrip-check-all">
                  <input
                    type="checkbox"
                    checked={allIds.length > 0 && allIds.every((id) => sel.selected.has(id))}
                    onChange={() => {
                      if (allIds.every((id) => sel.selected.has(id))) sel.clear();
                      else sel.replace(allIds);
                    }}
                  />
                  全选
                </label>
                <span className="filmstrip-count">
                  {shots.length} 镜 · 勾选 {sel.selected.size}
                  {selected ? ` · 检视 #${String(selected.storyboard_number).padStart(2, "0")}` : ""}
                </span>
                <div style={{ flex: 1 }} />
                <button
                  type="button"
                  className="toolbar-button"
                  disabled={!sel.selected.size || polishing || false}
                  onClick={() => void polishSelectedPrompts()}
                >
                  {polishing ? <Loader2 size={12} className="spin" /> : <Sparkles size={12} />}
                  AI 润色
                </button>
                {onGoto ? (
                  <button
                    type="button"
                    className="export-button"
                    style={{ minWidth: 100, padding: "7px 12px" }}
                    onClick={() => onGoto("timeline")}
                    title="成片台：多参考图 + 提示词语音出视频"
                  >
                    <Sparkles size={13} /> 去成片出视频
                  </button>
                ) : null}
              </div>
              <div className="filmstrip-scroll" ref={stripRef}>
                {ordered.map((s) => {
                  const active = selectedId === s.id;
                  const picked = sel.selected.has(s.id);
                  const busy = false;
                  return (
                    <button
                      key={`strip-${s.id}-n${s.storyboard_number}`}
                      type="button"
                      data-shot={s.id}
                      data-number={s.storyboard_number}
                      className={`filmstrip-shot${active ? " active" : ""}${picked ? " picked" : ""}${busy ? " busy" : ""}`}
                      onClick={() => focusShot(s.id)}
                      title={
                        busy
                          ? `${shotNumLabel(s.storyboard_number)} · id=${s.id}`
                          : `${shotNumLabel(s.storyboard_number)} ${s.title || ""} · id=${s.id}${active ? " · 当前检视" : ""}`
                      }
                    >
                      <span
                        className="filmstrip-shot-check"
                        onClick={(e) => {
                          e.stopPropagation();
                          sel.toggle(s.id);
                        }}
                      >
                        <input type="checkbox" checked={picked} readOnly />
                      </span>
                      <span className="filmstrip-shot-media">
                        {s.image_url ? (
                          <img src={s.image_url} alt={shotNumLabel(s.storyboard_number)} />
                        ) : (
                          <ImageIcon size={18} />
                        )}
                        {busy && (
                          <span className="filmstrip-busy">
                            <Loader2 size={14} className="spin" />
                          </span>
                        )}
                      </span>
                      <span className="filmstrip-shot-label">
                        {shotNumLabel(s.storyboard_number)}
                        {busy ? " ·出" : active ? " ·视" : ""}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: 12 }}>
          {loading ? (
            <div style={{ color: "var(--text3)", fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}>
              <Loader2 size={14} className="spin" /> 加载分镜…
            </div>
          ) : shots.length === 0 ? (
            <div style={{ color: "var(--text3)", fontSize: 13, padding: "24px 0", textAlign: "center" }}>
              还没有镜头脚本。点右上「AI 拆镜头列表」，再写提示词/台词，到成片台多图参考出视频。
              <div style={{ fontSize: 11, marginTop: 6 }}>（需要该分集已生成剧本）</div>
            </div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 12 }}>
              {shots.map((s) => {
                const picked = sel.selected.has(s.id);
                return (
                  <div
                    key={s.id}
                    className="card"
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      cursor: "pointer",
                      ...(picked ? { outline: "2px solid var(--green)", outlineOffset: -1 } : {}),
                      ...(selectedId === s.id ? { boxShadow: "0 0 0 1px var(--blue)" } : {}),
                    }}
                    onClick={() => setSelectedId(s.id)}
                  >
                    <div
                      style={{
                        position: "relative",
                        aspectRatio: "16/9",
                        background: "var(--surface)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        overflow: "hidden",
                      }}
                    >
                      <label
                        style={{
                          position: "absolute",
                          left: 6,
                          top: 6,
                          zIndex: 2,
                          display: "flex",
                          cursor: "pointer",
                          background: "var(--bg)",
                          borderRadius: 4,
                          padding: 3,
                          lineHeight: 0,
                        }}
                        title="选择此镜头（润色提示词等）"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <input type="checkbox" checked={picked} onChange={() => sel.toggle(s.id)} disabled={false} />
                      </label>
                      {s.image_url ? (
                        <img
                          src={s.image_url}
                          alt={s.title ?? ""}
                          onClick={(e) => {
                            // 单击选中卡片；双击再放大（与画布模式一致）
                            if (e.detail >= 2) {
                              e.stopPropagation();
                              setPreview(s.image_url);
                              return;
                            }
                            setSelectedId(s.id);
                          }}
                          onDoubleClick={(e) => {
                            e.stopPropagation();
                            setPreview(s.image_url);
                          }}
                          title="单击选中 · 双击放大"
                          style={{ width: "100%", height: "100%", objectFit: "cover", cursor: "pointer" }}
                        />
                      ) : (
                        <ImageIcon size={24} color="var(--text3)" />
                      )}
                      <span
                        style={{
                          position: "absolute",
                          right: 6,
                          top: 6,
                          fontSize: 11,
                          padding: "1px 6px",
                          borderRadius: 4,
                          background: "var(--bg)",
                          color: "var(--text2)",
                        }}
                      >
                        镜头 {String(s.storyboard_number).padStart(2, "0")}
                      </span>
                      {false && (
                        <span style={{ position: "absolute", right: 6, bottom: 6, zIndex: 2, color: "var(--green-t)" }}>
                          <Loader2 size={16} className="spin" />
                        </span>
                      )}
                    </div>
                    <div style={{ padding: "8px 10px", display: "flex", flexDirection: "column", gap: 5, flex: 1 }}>
                      <div style={{ fontSize: 12, fontWeight: 500 }}>{s.title || `镜头 ${s.storyboard_number}`}</div>
                      <div style={{ fontSize: 11, color: "var(--text3)" }}>
                        {[s.shot_type, s.location, `${clampShotDuration(s.duration)}s`].filter(Boolean).join(" · ")}
                      </div>
                      {s.action && <div style={{ fontSize: 11, color: "var(--text2)", lineHeight: 1.4 }}>{s.action}</div>}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          {mode === "grid" && selected && (
            <div
              style={{
                position: "sticky",
                bottom: 0,
                marginTop: 12,
                padding: 12,
                border: "1px solid var(--border)",
                borderRadius: 10,
                background: "var(--panel2)",
                maxHeight: 360,
                overflow: "auto",
              }}
            >
              {renderInspector()}
            </div>
          )}
        </div>
      )}
      <ImageLightbox src={preview} onClose={() => setPreview(null)} />
    </div>
  );
}
