import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Captions,
  ChevronLeft,
  ChevronRight,
  Download,
  Film,
  Image as ImageIcon,
  LayoutGrid,
  LoaderCircle,
  Minus,
  Music2,
  Play,
  Plus,
  Video,
  ZoomIn,
} from "lucide-react";
import {
  batchGenerateVideo,
  exportTimeline,
  generateVideo,
  getTimeline,
  listCharacters,
  listEpisodes,
  listProjects,
  listStoryboardVideos,
  listStoryboards,
  mediaDisplayUrl,
  runProductionSupervise,
  saveTimeline,
  selectVideoVersion,
  setVideoTrim,
  updateStoryboard,
  type CharacterAsset,
  type EpisodeSummary,
  type Project,
  type TimelineClip,
  type TimelineDocument,
  type TimelineTrack,
  type TimelineTrackName,
} from "../api/client";
import MediaUploadButton from "../components/MediaUploadButton";
import {
  InfiniteCanvas,
  type CanvasPoint,
  type InfiniteCanvasHandle,
  useCanvasNodeDrag,
} from "../components/InfiniteCanvas";
import { useSelection } from "../components/useSelection";

const NODE_W = 228;
/** 含段落按钮/状态条的实际卡片高度，必须 ≥ CSS .tl-node 内容，否则上下会盖住 */
const NODE_H = 312;
const COL_GAP = 40;
const ROW_GAP = 56;
const COLS = 4;
/** 段落行额外下边距，整段与下行分开 */
const SEGMENT_ROW_EXTRA = 32;

type Positions = Record<number, CanvasPoint>;

const TRACKS: { name: TimelineTrackName; label: string; color: string; icon: typeof Video }[] = [
  { name: "video", label: "视频(含模型语音)", color: "var(--blue)", icon: Video },
  { name: "subtitle", label: "字幕", color: "var(--amber)", icon: Captions },
  { name: "music", label: "音乐", color: "#a879e8", icon: Music2 },
];

function formatTime(seconds: number) {
  const value = Math.max(seconds, 0);
  const minutes = Math.floor(value / 60);
  return `${String(minutes).padStart(2, "0")}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
}

/** 成片台媒体地址：统一走 mediaDisplayUrl，可选缓存破坏（上传后必须 bust） */
function mediaUrl(url: string | null | undefined, bustCache = false) {
  return mediaDisplayUrl(url, bustCache);
}

/** v4：加大行高防上下遮挡；旧坐标作废 */
function posKey(episodeId: number) {
  return `xigua-tl-canvas-pos-v4:${episodeId}`;
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
    /* ignore */
  }
}

function orderedClips(clips: TimelineClip[]): TimelineClip[] {
  return [...clips]
    .filter((c) => c.storyboard_id != null)
    .sort((a, b) => a.index - b.index || (a.storyboard_id ?? 0) - (b.storyboard_id ?? 0));
}

/**
 * 成片画布自动排布（保证卡片上下互不遮挡）：
 * - 行高 = NODE_H + ROW_GAP，NODE_H 按卡片真实高度预留
 * - 运镜段落按 part 横排，超过 COLS 自动折行，行间距同样拉开
 * - 非段落镜头按 COLS 网格
 */
function autoLayout(clips: TimelineClip[]): Positions {
  const ordered = orderedClips(clips);
  const next: Positions = {};
  type Row = { clips: TimelineClip[]; isSegment: boolean };
  const rows: Row[] = [];
  let i = 0;
  while (i < ordered.length) {
    const cur = ordered[i];
    const isSeg = Boolean(cur.segment_key && cur.segment_total && cur.segment_total >= 2);
    if (isSeg && cur.segment_key) {
      const key = cur.segment_key;
      const group: TimelineClip[] = [];
      while (i < ordered.length && ordered[i].segment_key === key) {
        group.push(ordered[i]);
        i += 1;
      }
      group.sort(
        (a, b) =>
          (a.segment_part ?? 0) - (b.segment_part ?? 0) || a.index - b.index,
      );
      // 段落超过一屏宽度时折行，避免挤在一起又被下一行盖住
      for (let j = 0; j < group.length; j += COLS) {
        rows.push({ clips: group.slice(j, j + COLS), isSegment: true });
      }
    } else {
      const group: TimelineClip[] = [];
      while (
        i < ordered.length &&
        !(ordered[i].segment_key && (ordered[i].segment_total ?? 0) >= 2)
      ) {
        group.push(ordered[i]);
        i += 1;
        if (group.length >= COLS) break;
      }
      rows.push({ clips: group, isSegment: false });
    }
  }

  let y = 24;
  for (const row of rows) {
    let x = 24;
    row.clips.forEach((clip) => {
      const id = clip.storyboard_id!;
      next[id] = { x, y };
      x += NODE_W + COL_GAP;
    });
    // 固定步长：下一行顶部 = 本行顶部 + 卡片高 + 间距，绝不重叠
    y += NODE_H + ROW_GAP + (row.isSegment ? SEGMENT_ROW_EXTRA : 0);
  }
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

/** 结构签名：镜数量/顺序/段落变化时强制重排 */
function layoutSignature(clips: TimelineClip[]): string {
  return orderedClips(clips)
    .map(
      (c) =>
        `${c.storyboard_id}:${c.index}:${c.segment_key ?? ""}:${c.segment_part ?? ""}`,
    )
    .join("|");
}

function layoutNeedsForce(clips: TimelineClip[], saved: Positions): boolean {
  const ordered = orderedClips(clips);
  if (!ordered.length) return false;
  const boxes: { id: number; x: number; y: number }[] = [];
  for (const c of ordered) {
    const id = c.storyboard_id!;
    const p = saved[id];
    if (!p) return true;
    boxes.push({ id, x: p.x, y: p.y });
  }
  // 任意两卡矩形相交（含上下盖住）→ 重排
  for (let a = 0; a < boxes.length; a++) {
    for (let b = a + 1; b < boxes.length; b++) {
      const A = boxes[a];
      const B = boxes[b];
      const overlapX = A.x < B.x + NODE_W - 4 && A.x + NODE_W - 4 > B.x;
      const overlapY = A.y < B.y + NODE_H - 4 && A.y + NODE_H - 4 > B.y;
      if (overlapX && overlapY) return true;
    }
  }
  return false;
}

/** 相邻镜头连线（时间顺序） */
function TimelineEdge({ from, to, solid }: { from: CanvasPoint; to: CanvasPoint; solid?: boolean }) {
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
  const stroke = solid ? "rgba(72, 201, 176, 0.7)" : "rgba(91,141,239,0.28)";
  return (
    <svg
      className="tl-edge"
      width={Math.max(w, 8)}
      height={Math.max(h, 8)}
      style={{ left: minX, top: minY, position: "absolute", pointerEvents: "none", zIndex: 0 }}
      viewBox={`${minX} ${minY} ${Math.max(w, 8)} ${Math.max(h, 8)}`}
    >
      <path d={d} fill="none" stroke={stroke} strokeWidth={solid ? 2.5 : 1.5} strokeDasharray={solid ? undefined : "5 4"} />
    </svg>
  );
}

/** ComfyUI 单镜上限 5 秒：成片台所有 clip 时长统一钳制 */
const CLIP_MAX_DURATION = 5;

function clampClipDuration(value: number): number {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return Math.min(n, CLIP_MAX_DURATION);
}

function clampTimelineDocument(document: TimelineDocument): TimelineDocument {
  return synchronise(document, document.tracks.video.clips);
}

function synchronise(document: TimelineDocument, videoClips: TimelineClip[]): TimelineDocument {
  let start = 0;
  const timing = new Map<number, { index: number; start: number; duration: number }>();
  const video = videoClips.map((clip, index) => {
    const next = { ...clip, index, start, duration: clampClipDuration(clip.duration) };
    if (next.storyboard_id != null) timing.set(next.storyboard_id, next);
    start += next.duration;
    return next;
  });
  const syncTrack = (track: TimelineTrack) => ({
    ...track,
    clips: track.clips
      .filter((clip) => clip.storyboard_id != null && timing.has(clip.storyboard_id))
      .sort((left, right) => timing.get(left.storyboard_id!)!.index - timing.get(right.storyboard_id!)!.index)
      .map((clip) => ({ ...clip, ...timing.get(clip.storyboard_id!)! })),
  });
  return {
    ...document,
    duration: start,
    tracks: {
      ...document.tracks,
      video: { ...document.tracks.video, clips: video },
      voiceover: syncTrack(document.tracks.voiceover),
      subtitle: syncTrack(document.tracks.subtitle),
    },
  };
}

function ClipNode({
  clip,
  pos,
  selected,
  picked,
  generating,
  zoomRef,
  onSelect,
  onTogglePick,
  onMove,
  onMoveEnd,
  onQuickFinal,
  onSelectSegment,
}: {
  clip: TimelineClip;
  pos: CanvasPoint;
  selected: boolean;
  picked: boolean;
  generating: boolean;
  zoomRef: React.RefObject<number>;
  onSelect: () => void;
  onTogglePick: () => void;
  onMove: (id: number, next: CanvasPoint) => void;
  onMoveEnd: (id: number, next: CanvasPoint) => void;
  onQuickFinal?: () => void;
  onSelectSegment?: () => void;
}) {
  const id = clip.storyboard_id!;
  const drag = useCanvasNodeDrag(
    id,
    pos,
    zoomRef,
    (nid, next) => onMove(nid as number, next),
    (nid, next) => onMoveEnd(nid as number, next),
  );
  const thumb = mediaUrl(clip.thumbnail);
  const hasImage = Boolean(thumb);
  const hasVideo = Boolean(clip.video_url);
  const hasDialogue = Boolean((clip.subtitle_text || "").trim());
  const hasTrim = clip.trim_in != null || clip.trim_out != null;
  // 管线阶段：图 → 视频（模型语音随视频生成）
  const stageMissing = !hasImage ? "image" : !hasVideo ? "video" : "ready";
  const stageColor =
    stageMissing === "ready" ? "var(--green)" : stageMissing === "image" ? "var(--amber)" : "#5b8def";
  const inSegment = Boolean(clip.segment_key && clip.segment_total && clip.segment_total >= 2);
  let segHue = "transparent";
  if (clip.segment_key) {
    let hash = 0;
    for (let i = 0; i < clip.segment_key.length; i++) hash = (hash * 31 + clip.segment_key.charCodeAt(i)) >>> 0;
    segHue = `hsl(${hash % 360} 62% 48%)`;
  }

  return (
    <div
      className={`tl-node${selected ? " selected" : ""}${picked ? " picked" : ""}${inSegment ? " in-segment" : ""}`}
      style={{
        left: pos.x,
        top: pos.y,
        zIndex: selected || picked ? 6 : 2,
        boxShadow: inSegment ? `inset 3px 0 0 ${segHue}` : undefined,
        outline: selected ? undefined : `1px solid ${stageColor}33`,
      }}
      onPointerDown={drag.onPointerDown}
      onClick={(e) => {
        e.stopPropagation();
        onSelect();
      }}
    >
      <div className="tl-node-head">
        <label
          data-no-drag
          style={{ display: "flex", cursor: "pointer", margin: 0, padding: 2 }}
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
        >
          <input type="checkbox" checked={picked} onChange={onTogglePick} />
        </label>
        <strong title={`时间线序号 #${clip.index + 1} · 分镜 id=${clip.storyboard_id}`}>
          #{String(clip.index + 1).padStart(2, "0")}
        </strong>
        <span className="tl-node-badge">{clip.duration.toFixed(1)}s</span>
      </div>
      {/* 节点管线状态条：图 / 测 / 定 / 音 */}
      <div
        data-no-drag
        style={{ display: "flex", gap: 3, padding: "0 8px 4px", fontSize: 9 }}
        onClick={(e) => e.stopPropagation()}
      >
        <span title="分镜图" style={{ color: hasImage ? "var(--green)" : "var(--text3)" }}>
          图{hasImage ? "✓" : "·"}
        </span>
        <span title="视频" style={{ color: hasVideo ? "var(--green)" : "var(--text3)" }}>
          视{hasVideo ? "✓" : "·"}
        </span>
        <span title="台词（语音随出片由模型生成）" style={{ color: hasDialogue ? "var(--green)" : "var(--text3)" }}>
          白{hasDialogue ? "✓" : "—"}
        </span>
        {hasTrim && (
          <span title="已设裁切" style={{ color: "var(--amber)" }}>
            裁
          </span>
        )}
      </div>
      <div className="tl-node-preview">
        {hasVideo ? (
          <video
            key={clip.video_url || undefined}
            src={mediaUrl(clip.video_url)}
            poster={thumb || undefined}
            muted
            preload="metadata"
          />
        ) : thumb ? (
          <img key={thumb} src={thumb} alt="" draggable={false} />
        ) : (
          <ImageIcon size={22} color="var(--text3)" />
        )}
        {generating && (
          <span className="tl-node-busy">
            <LoaderCircle size={16} className="spin" />
          </span>
        )}
        <div className="tl-node-flags">
          {hasVideo && <span className="ok">视频</span>}
          {!hasVideo && !thumb && <span className="warn">缺图</span>}
          {stageMissing === "ready" && <span className="ok">就绪</span>}
        </div>
      </div>
      <div className="tl-node-body">
        {inSegment && (
          <div
            style={{
              fontSize: 10,
              color: "#fff",
              background: segHue,
              borderRadius: 3,
              padding: "0 5px",
              marginBottom: 4,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={clip.segment_title || "运镜段落"}
          >
            段落 {clip.segment_part}/{clip.segment_total}
            {clip.segment_title ? ` · ${clip.segment_title}` : ""}
          </div>
        )}
        <div className="tl-node-meta">
          {formatTime(clip.start)} ·{" "}
          {stageMissing === "ready"
            ? "可导出"
            : stageMissing === "image"
              ? "先备角色/场景图"
              : "待出视频"}
        </div>
        {clip.subtitle_text && <div className="tl-node-sub">{clip.subtitle_text}</div>}
        {inSegment && onSelectSegment && (
          <div
            data-no-drag
            style={{ display: "flex", gap: 4, marginTop: 4 }}
            onClick={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="btn-secondary"
              style={{ fontSize: 10, padding: "2px 6px", flex: 1 }}
              onClick={onSelectSegment}
              title={`勾选本段落全部 ${clip.segment_total} 镜，再点底部批量出视频`}
            >
              选本段{clip.segment_total ? `×${clip.segment_total}` : ""}
            </button>
          </div>
        )}
        {hasImage && !generating && onQuickFinal && (
          <div
            data-no-drag
            style={{ display: "flex", gap: 4, marginTop: 4 }}
            onClick={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
          >
            <button type="button" className="btn-primary" style={{ fontSize: 10, padding: "2px 6px", flex: 1 }} onClick={onQuickFinal}>
              定本镜
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default function TimelineView({
  currentDramaId,
  currentEpisodeId,
  username = "local",
}: {
  currentDramaId?: number | null;
  currentEpisodeId?: number | null;
  username?: string;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [episodes, setEpisodes] = useState<EpisodeSummary[]>([]);
  const [episodeId, setEpisodeId] = useState<number | null>(null);
  const [timeline, setTimeline] = useState<TimelineDocument | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [notice, setNotice] = useState("");
  const [noticeKind, setNoticeKind] = useState<"normal" | "error">("normal");
  const [mergedUrl, setMergedUrl] = useState("");
  /** 单镜出片：记录镜头 id + 模式，避免定稿/说话按钮同时转圈 */
  const [generatingVideo, setGeneratingVideo] = useState<{ id: number; mode: "final" | "talking" } | null>(null);
  /** 批量出片进行中 */
  const [batchRunning, setBatchRunning] = useState<"final" | null>(null);
  const [videoPrompts, setVideoPrompts] = useState<Record<number, string>>({});
  /** 上传后强制 <video>/<img> 重挂载，避免浏览器缓存旧海报/旧片 */
  const [mediaRev, setMediaRev] = useState(0);
  const [characters, setCharacters] = useState<CharacterAsset[]>([]);
  const [speakingChars, setSpeakingChars] = useState<Record<number, number | null>>({});
  const [positions, setPositions] = useState<Positions>({});
  const [zoomLabel, setZoomLabel] = useState(85);
  const [showTracks, setShowTracks] = useState(false);
  const autoSaveTimer = useRef<number | null>(null);
  const canvasRef = useRef<InfiniteCanvasHandle>(null);
  const zoomRef = useRef(0.85);
  const fittedRef = useRef<number | null>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  const sel = useSelection();

  useEffect(() => {
    listProjects()
      .then((rows) => {
        setProjects(rows);
        const preferred =
          currentDramaId != null && rows.some((r) => r.id === currentDramaId) ? currentDramaId : rows[0]?.id ?? null;
        setProjectId(preferred);
      })
      .catch((error: Error) => setNotice(error.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentDramaId]);

  useEffect(() => {
    if (projectId == null) {
      setEpisodes([]);
      setEpisodeId(null);
      return;
    }
    listEpisodes(projectId)
      .then((rows) => {
        setEpisodes(rows);
        const preferred =
          currentEpisodeId != null && rows.some((r) => r.id === currentEpisodeId)
            ? currentEpisodeId
            : rows[0]?.id ?? null;
        setEpisodeId(preferred);
      })
      .catch((error: Error) => setNotice(error.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, currentEpisodeId]);

  useEffect(() => {
    if (projectId == null) {
      setCharacters([]);
      return;
    }
    listCharacters(projectId).then(setCharacters).catch(() => setCharacters([]));
  }, [projectId]);

  useEffect(() => {
    if (episodeId == null) {
      setSpeakingChars({});
      return;
    }
    listStoryboards(episodeId)
      .then((rows) => setSpeakingChars(Object.fromEntries(rows.map((s) => [s.id, s.speaking_character_id]))))
      .catch(() => setSpeakingChars({}));
  }, [episodeId]);

  useEffect(
    () => () => {
      if (autoSaveTimer.current != null) window.clearTimeout(autoSaveTimer.current);
    },
    [],
  );

  const layoutSigRef = useRef<string>("");

  const applyLayout = useCallback((clips: TimelineClip[], epId: number, force = false) => {
    const ordered = orderedClips(clips);
    const sig = layoutSignature(ordered);
    const saved = force ? {} : loadPositions(epId);
    const shouldForce = force || layoutNeedsForce(ordered, saved) || layoutSigRef.current !== sig;
    const layout = autoLayout(ordered);
    const merged: Positions = {};
    ordered.forEach((c) => {
      const id = c.storyboard_id!;
      // 结构变化时一律用新自动布局，避免拆镜后旧坐标把 #7 甩到角落
      merged[id] = shouldForce ? layout[id] : (saved[id] ?? layout[id]);
    });
    layoutSigRef.current = sig;
    setPositions(merged);
    savePositions(epId, merged);
    return merged;
  }, []);

  useEffect(() => {
    if (episodeId == null) {
      setTimeline(null);
      setPositions({});
      layoutSigRef.current = "";
      return;
    }
    let active = true;
    setLoading(true);
    setNotice("");
    setMergedUrl("");
    fittedRef.current = null;
    layoutSigRef.current = "";
    sel.clear();
    getTimeline(episodeId)
      .then((document) => {
        if (!active) return;
        const clamped = clampTimelineDocument(document);
        // 保证 video 轨按 index 排序
        const sorted = {
          ...clamped,
          tracks: {
            ...clamped.tracks,
            video: {
              ...clamped.tracks.video,
              clips: orderedClips(clamped.tracks.video.clips).map((c, i) => ({ ...c, index: i })),
            },
          },
        };
        const synced = synchronise(sorted, sorted.tracks.video.clips);
        setTimeline(synced);
        const first = synced.tracks.video.clips[0]?.storyboard_id ?? null;
        setSelectedId(first);
        // 进页强制规整排布（段落分行 + 镜序）
        applyLayout(synced.tracks.video.clips, episodeId, true);
      })
      .catch((error: Error) => active && setNotice(error.message))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [episodeId]);

  const videoClips = timeline?.tracks.video.clips ?? [];
  const clipIds = useMemo(
    () => videoClips.map((c) => c.storyboard_id).filter((id): id is number => id != null),
    [videoClips],
  );

  // 仅在进入分集时 fit 一次；positions 后续变化不再自动缩放（避免选中/出片后乱跳）
  useEffect(() => {
    if (!timeline || episodeId == null || loading || !clipIds.length) return;
    if (fittedRef.current === episodeId) return;
    const t = window.setTimeout(() => {
      canvasRef.current?.fitView(boundsOf(positions, clipIds), 56);
      fittedRef.current = episodeId;
    }, 60);
    return () => clearTimeout(t);
    // 故意不依赖 positions：布局完成后由 applyLayout 触发一次 positions 更新即可
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeline, episodeId, loading, clipIds.length]);

  useEffect(() => {
    if (selectedId == null || !stripRef.current) return;
    const parent = stripRef.current;
    const el = parent.querySelector(`[data-shot="${selectedId}"]`) as HTMLElement | null;
    if (!el) return;
    // 只滚胶片条本身，且仅在目标在条外时滚，避免整页/画布跟着抖
    const pRect = parent.getBoundingClientRect();
    const eRect = el.getBoundingClientRect();
    if (eRect.left < pRect.left + 8 || eRect.right > pRect.right - 8) {
      el.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    }
  }, [selectedId]);

  const selected = useMemo(
    () => timeline?.tracks.video.clips.find((clip) => clip.storyboard_id === selectedId) ?? null,
    [timeline, selectedId],
  );
  const selectedSubtitle = useMemo(
    () => timeline?.tracks.subtitle.clips.find((clip) => clip.storyboard_id === selectedId) ?? null,
    [timeline, selectedId],
  );
  const focusShot = (id: number) => {
    setSelectedId(id);
    // 不改缩放、不 fitView：仅当卡片在视口外时轻推一下，已在视野内则完全不动
    const p = positions[id];
    if (p && canvasRef.current?.ensureVisible) {
      canvasRef.current.ensureVisible(
        { minX: p.x, minY: p.y, maxX: p.x + NODE_W, maxY: p.y + NODE_H },
        40,
      );
    }
  };

  const reorder = (offset: number) => {
    if (!timeline || selectedId == null || episodeId == null) return;
    const clips = orderedClips(timeline.tracks.video.clips);
    const index = clips.findIndex((clip) => clip.storyboard_id === selectedId);
    const target = index + offset;
    if (index < 0 || target < 0 || target >= clips.length) return;
    [clips[index], clips[target]] = [clips[target], clips[index]];
    const next = synchronise(timeline, clips);
    setTimeline(next);
    // 重排后按新镜序重铺画布
    applyLayout(next.tracks.video.clips, episodeId, true);
  };

  const scheduleStoryboardSave = (
    storyboardId: number,
    changes: { dialogue?: string; duration?: number },
    document: TimelineDocument,
  ) => {
    if (autoSaveTimer.current != null) window.clearTimeout(autoSaveTimer.current);
    autoSaveTimer.current = window.setTimeout(async () => {
      try {
        await updateStoryboard(storyboardId, changes);
        if (episodeId != null) await saveTimeline(episodeId, document);
      } catch (error) {
        setNoticeKind("error");
        setNotice(error instanceof Error ? error.message : "自动保存失败");
      }
    }, 500);
  };

  const updateDuration = (duration: number) => {
    if (!timeline || selectedId == null) return;
    // 与 ComfyUI 单镜上限一致：1–5 秒
    const clamped = Math.min(Math.max(Math.round(duration) || 1, 1), 5);
    const clips = timeline.tracks.video.clips.map((clip) =>
      clip.storyboard_id === selectedId ? { ...clip, duration: clamped } : clip,
    );
    const next = synchronise(timeline, clips);
    setTimeline(next);
    scheduleStoryboardSave(selectedId, { duration: clamped }, next);
  };

  const updateSubtitle = (subtitleText: string) => {
    if (!timeline || selectedId == null || !selected) return;
    const exists = timeline.tracks.subtitle.clips.some((clip) => clip.storyboard_id === selectedId);
    const clips = exists
      ? timeline.tracks.subtitle.clips.map((clip) =>
          clip.storyboard_id === selectedId ? { ...clip, subtitle_text: subtitleText } : clip,
        )
      : [...timeline.tracks.subtitle.clips, { ...selected, video_url: null, audio_url: null, subtitle_text: subtitleText }];
    const next = {
      ...timeline,
      tracks: {
        ...timeline.tracks,
        subtitle: { ...timeline.tracks.subtitle, clips },
      },
    };
    const synchronised = synchronise(next, next.tracks.video.clips);
    setTimeline(synchronised);
    scheduleStoryboardSave(selectedId, { dialogue: subtitleText }, synchronised);
  };

  const toggleTrack = (name: TimelineTrackName) => {
    if (!timeline) return;
    setTimeline({
      ...timeline,
      tracks: {
        ...timeline.tracks,
        [name]: { ...timeline.tracks[name], enabled: !timeline.tracks[name].enabled },
      },
    });
  };

  const save = async () => {
    if (!timeline || episodeId == null) return null;
    setSaving(true);
    setNotice("");
    try {
      const saved = clampTimelineDocument(await saveTimeline(episodeId, timeline));
      setTimeline(saved);
      setNoticeKind("normal");
      setNotice("时间线已保存");
      return saved;
    } catch (error) {
      setNoticeKind("error");
      setNotice(error instanceof Error ? error.message : "保存失败");
      return null;
    } finally {
      setSaving(false);
    }
  };

  const exportMovie = async (asyncMode = false) => {
    if (!timeline || episodeId == null) return;
    setExporting(true);
    setNotice("");
    setMergedUrl("");
    try {
      const saved = await save();
      if (!saved) return;
      const result = await exportTimeline(episodeId, { username, async_mode: asyncMode });
      if (result.async && result.job) {
        setNoticeKind("normal");
        setNotice(`成片导出已入队 #${result.job.id}（含 trim 裁切）· 顶栏「任务」查看`);
        return;
      }
      if (result.status === "completed" && result.merged_url) {
        setMergedUrl(mediaUrl(result.merged_url));
        setNoticeKind("normal");
        setNotice("成片导出完成（已应用各镜 trim 入出点）");
      } else {
        setNoticeKind("error");
        setNotice(result.error || "导出失败");
        if (result.blocked && result.clip_index != null)
          setSelectedId(saved.tracks.video.clips[result.clip_index]?.storyboard_id ?? null);
      }
    } catch (error) {
      setNoticeKind("error");
      setNotice(error instanceof Error ? error.message : "导出失败");
    } finally {
      setExporting(false);
    }
  };

  const reloadTimeline = async () => {
    if (episodeId == null) return;
    const raw = clampTimelineDocument(await getTimeline(episodeId));
    const ordered = orderedClips(raw.tracks.video.clips).map((c, i) => ({ ...c, index: i }));
    const doc = synchronise(
      { ...raw, tracks: { ...raw.tracks, video: { ...raw.tracks.video, clips: ordered } } },
      ordered,
    );
    setTimeline(doc);
    // 结构变了会 force；仅媒体更新则保留用户拖拽位置
    applyLayout(doc.tracks.video.clips, episodeId, false);
  };

  const setSpeakingCharacter = async (storyboardId: number, characterId: number | null) => {
    const previous = speakingChars[storyboardId] ?? null;
    setSpeakingChars((prev) => ({ ...prev, [storyboardId]: characterId }));
    try {
      await updateStoryboard(storyboardId, { speaking_character_id: characterId });
      setNoticeKind("normal");
      setNotice(characterId == null ? "已取消说话角色绑定" : "说话角色已绑定");
    } catch (error) {
      setSpeakingChars((prev) => ({ ...prev, [storyboardId]: previous }));
      setNoticeKind("error");
      setNotice(error instanceof Error ? error.message : "绑定说话角色失败");
    }
  };

  /** ComfyUI 单镜图生视频上限 5 秒，与后端 clamp_video_duration 一致 */
  const clampAiVideoDuration = (durationSec: number) =>
    Math.min(Math.max(Math.round(durationSec) || 5, 2), 5);

  /** 定稿出片固定 5s（MiniMax H3 图生视频） */
  /** 默认 3 秒加快 H3；需要 5 秒可在时长输入框改 */
  const VIDEO_CLIP_SECONDS = 3;

  const generateOneVideo = (
    storyboardId: number,
    durationSec: number = VIDEO_CLIP_SECONDS,
    asyncMode = false,
  ) =>
    generateVideo({
      storyboard_id: storyboardId,
      username,
      duration: clampAiVideoDuration(durationSec),
      quality_mode: "final",
      prompt: videoPrompts[storyboardId]?.trim() || undefined,
      // 有本镜图时后端会优先本镜图；尾帧仅作无图时的衔接
      use_prev_last_frame: true,
      async_mode: asyncMode,
    });

  const videoBusy = generatingVideo != null || batchRunning != null;

  const generateVideoForSelected = async () => {
    if (selected?.storyboard_id == null || episodeId == null) return;
    const targetId = selected.storyboard_id;
    const shotNo = selected.index + 1;
    const label = "定稿出片（MiniMax H3）";
    const dialogueText = (selected.subtitle_text || "").trim();
    if (!dialogueText) {
      const ok = window.confirm(
        `镜头 #${String(shotNo).padStart(2, "0")} 没有台词。\n\n` +
          "继续出片将强制「闭嘴、无对白」，只保留环境音。\n" +
          "若角色要说话，请先在右侧填「角色名：台词」再出片。\n\n确定继续？",
      );
      if (!ok) return;
    }
    setGeneratingVideo({ id: targetId, mode: "final" });
    setNotice("");
    setNoticeKind("normal");
    try {
      const result = await generateOneVideo(targetId, VIDEO_CLIP_SECONDS, false);
      if (result.async && result.job) {
        setNotice(`${label}已入队 #${result.job.id}（镜头 #${String(shotNo).padStart(2, "0")}）· 顶栏「任务」可看进度`);
        return;
      }
      if (result.status === "completed" && result.video_url) {
        if (result.storyboard_id != null && result.storyboard_id !== targetId) {
          setNoticeKind("error");
          setNotice(
            `出视频串镜：请求镜头 #${String(shotNo).padStart(2, "0")}(id=${targetId})，返回 id=${result.storyboard_id}`,
          );
          return;
        }
        await reloadTimeline();
        setSelectedId(targetId); // 重载后保持选中本镜，避免跳回 #01
        setNotice(
          `${label}完成 · 镜头 #${String(shotNo).padStart(2, "0")} · 语音由视频模型生成`,
        );
      } else {
        setNoticeKind("error");
        setNotice(result.error_msg || `${label}失败`);
      }
    } catch (error) {
      setNoticeKind("error");
      setNotice(error instanceof Error ? error.message : `${label}失败`);
    } finally {
      setGeneratingVideo(null);
    }
  };



  /** 同一运镜段落的全部镜头 id（按时间线顺序） */
  const segmentIds = (key: string | null | undefined) =>
    key
      ? videoClips
          .filter((c) => c.segment_key === key && c.storyboard_id != null)
          .map((c) => c.storyboard_id as number)
      : [];

  const selectSegment = (key: string | null | undefined) => {
    const ids = segmentIds(key);
    if (!ids.length) {
      setNoticeKind("error");
      setNotice("当前镜头不在运镜段落里");
      return;
    }
    sel.replace(ids);
    setNoticeKind("normal");
    setNotice(`已勾选本段落 ${ids.length} 镜，可点底部「批量定稿」一次出齐`);
  };

  const batchGenerateSelectedVideos = async (explicitIds?: number[]) => {
    if (!timeline) return;
    // 按时间线顺序，保证 1/5→5/5 衔接
    const idSet = new Set(explicitIds?.length ? explicitIds : [...sel.selected]);
    const ids = videoClips
      .map((c) => c.storyboard_id)
      .filter((id): id is number => id != null && idSet.has(id));
    if (!ids.length) {
      setNoticeKind("error");
      setNotice("请先勾选镜头：卡片左上角勾选，或点「选本段×5」再批量出视频");
      return;
    }
    const silentShots = videoClips.filter(
      (c) => c.storyboard_id != null && idSet.has(c.storyboard_id) && !(c.subtitle_text || "").trim(),
    );
    if (silentShots.length) {
      const preview = silentShots
        .slice(0, 6)
        .map((c) => `#${String(c.index + 1).padStart(2, "0")}`)
        .join(" ");
      const ok = window.confirm(
        `选中镜头里有 ${silentShots.length} 镜没有台词（${preview}${silentShots.length > 6 ? " …" : ""}）。\n\n` +
          "这些镜将闭嘴只出环境音。若角色要说话，请先在台词栏填「角色名：内容」。\n\n确定继续批量出片？",
      );
      if (!ok) return;
    }
    sel.replace(ids);
    const label = "批量定稿出片（MiniMax H3）";
    setBatchRunning("final");
    setNotice("");
    setNoticeKind("normal");
    try {
      // 批量默认异步入队 + 全局 Comfy 锁，避免 4060 并发 OOM
      const result = await batchGenerateVideo({
        storyboard_ids: ids,
        username,
        duration: VIDEO_CLIP_SECONDS,
        quality_mode: "final",
        use_prev_last_frame: true,
        async_mode: true,
      });
      if (result.async && result.job) {
        setNotice(`${label}已入队任务 #${result.job.id}（${ids.length} 镜）· 顶栏「任务」可看进度/取消`);
        return;
      }
      const rows = result.results || [];
      const ok = rows.filter((r) => r.status === "completed" || r.video_url).length;
      const fail = rows.length - ok;
      await reloadTimeline();
      if (fail) {
        setNoticeKind("error");
        setNotice(`${label}：成功 ${ok}，失败 ${fail}`);
      } else {
        setNotice(`${label}完成：${ok} 个镜头 · 各 5s`);
      }
    } catch (error) {
      setNoticeKind("error");
      setNotice(error instanceof Error ? error.message : `${label}失败`);
    } finally {
      setBatchRunning(null);
    }
  };

  const batchGenerateSegmentVideos = async (key: string | null | undefined) => {
    const ids = segmentIds(key);
    if (!ids.length) {
      setNoticeKind("error");
      setNotice("当前镜头不在运镜段落里，请用单镜定稿出片");
      return;
    }
    await batchGenerateSelectedVideos(ids);
  };

  const onNodeMove = (id: number, next: CanvasPoint) => {
    setPositions((prev) => ({ ...prev, [id]: next }));
  };

  const onNodeMoveEnd = (id: number, next: CanvasPoint) => {
    if (episodeId == null) return;
    setPositions((prev) => {
      const merged = { ...prev, [id]: next };
      savePositions(episodeId, merged);
      return merged;
    });
  };

  const relayout = () => {
    if (episodeId == null || !timeline) return;
    const layout = applyLayout(timeline.tracks.video.clips, episodeId, true);
    fittedRef.current = null;
    window.setTimeout(() => {
      const ids = orderedClips(timeline.tracks.video.clips)
        .map((c) => c.storyboard_id)
        .filter((id): id is number => id != null);
      canvasRef.current?.fitView(boundsOf(layout, ids), 56);
      fittedRef.current = episodeId;
    }, 40);
    setNoticeKind("normal");
    setNotice("已重排画布：行距加大，卡片上下不再遮挡；同段落横排，超出自动折行");
  };

  return (
    <div className="feature-view timeline-view">
      <div className="feature-header">
        <div>
          <h2>成片工作台</h2>
          <p>
            单镜 AI 视频最长 5 秒；整集成片 = 各镜相加
            {timeline && videoClips.length
              ? `（当前 ${videoClips.length} 镜 · 总时长 ${formatTime(timeline.duration)}）`
              : ""}
            · 尾帧衔接 · 批量出片 · 导出合镜
          </p>
        </div>
        <div className="feature-filters">
          <select value={projectId ?? ""} onChange={(event) => setProjectId(Number(event.target.value))}>
            {!projects.length && <option value="">暂无项目</option>}
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.title}
              </option>
            ))}
          </select>
          <select value={episodeId ?? ""} onChange={(event) => setEpisodeId(Number(event.target.value))}>
            {!episodes.length && <option value="">暂无分集</option>}
            {episodes.map((episode) => (
              <option key={episode.id} value={episode.id}>
                第 {episode.episode_number} 集 · {episode.title}
              </option>
            ))}
          </select>
          <button
            type="button"
            className={`toolbar-button${showTracks ? " active" : ""}`}
            onClick={() => setShowTracks((v) => !v)}
            title="显示/隐藏多轨时间线"
          >
            <Film size={14} /> 多轨
          </button>
        </div>
      </div>

      {notice && (
        <div className={`feature-notice${noticeKind === "error" ? " error" : ""}`}>
          <AlertTriangle size={14} /> {notice}
        </div>
      )}

      <div className="timeline-workbench">
        <div className="timeline-main">
          <div className="timeline-canvas-wrap">
            {loading ? (
              <div className="timeline-canvas-empty">
                <LoaderCircle size={18} className="spin" /> 加载时间线…
              </div>
            ) : !videoClips.length ? (
              <div className="timeline-canvas-empty">
                <Film size={32} />
                <div>当前分集还没有分镜镜头</div>
                <div style={{ fontSize: 11, opacity: 0.75 }}>
                  请到「镜头脚本」拆列表并写提示词/台词；备好角色/场景定妆图后，在此多图参考生视频
                </div>
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
                  {/* 镜序连线：同段落实线，跨段落虚线 */}
                  {orderedClips(videoClips).map((clip, idx, arr) => {
                    if (idx >= arr.length - 1) return null;
                    const a = clip;
                    const b = arr[idx + 1];
                    if (a.storyboard_id == null || b.storyboard_id == null) return null;
                    const from = positions[a.storyboard_id];
                    const to = positions[b.storyboard_id];
                    if (!from || !to) return null;
                    const solid = Boolean(
                      a.segment_key && a.segment_key === b.segment_key && (a.segment_total ?? 0) >= 2,
                    );
                    return (
                      <TimelineEdge
                        key={`e-${a.storyboard_id}-${b.storyboard_id}`}
                        from={from}
                        to={to}
                        solid={solid}
                      />
                    );
                  })}
                  {orderedClips(videoClips).map((clip) => {
                    if (clip.storyboard_id == null) return null;
                    const pos = positions[clip.storyboard_id] ?? { x: 0, y: 0 };
                    return (
                      <ClipNode
                        key={`tl-${clip.storyboard_id}-i${clip.index}`}
                        clip={clip}
                        pos={pos}
                        selected={selectedId === clip.storyboard_id}
                        picked={sel.selected.has(clip.storyboard_id)}
                        generating={generatingVideo?.id === clip.storyboard_id}
                        zoomRef={zoomRef}
                        onSelect={() => focusShot(clip.storyboard_id!)}
                        onTogglePick={() => sel.toggle(clip.storyboard_id!)}
                        onSelectSegment={
                          clip.segment_key ? () => selectSegment(clip.segment_key) : undefined
                        }
                        onMove={onNodeMove}
                        onMoveEnd={onNodeMoveEnd}
                        onQuickFinal={() => {
                          const sid = clip.storyboard_id!;
                          const no = clip.index + 1;
                          setSelectedId(sid);
                          void (async () => {
                            setGeneratingVideo({ id: sid, mode: "final" });
                            try {
                              const r = await generateOneVideo(sid, VIDEO_CLIP_SECONDS, false);
                              if (r.status === "completed") {
                                if (r.storyboard_id != null && r.storyboard_id !== sid) {
                                  setNotice(`串镜：请求 #${no} 返回 id=${r.storyboard_id}`);
                                } else {
                                  await reloadTimeline();
                                  setSelectedId(sid);
                                  setNotice(`定稿出片完成 镜头#${String(no).padStart(2, "0")}`);
                                }
                              } else setNotice(r.error_msg || "定稿出片失败");
                            } catch (e) {
                              setNotice(e instanceof Error ? e.message : "失败");
                            } finally {
                              setGeneratingVideo(null);
                            }
                          })();
                        }}
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
                    onClick={() => canvasRef.current?.fitView(boundsOf(positions, clipIds), 56)}
                  >
                    <ZoomIn size={14} />
                  </button>
                  <button type="button" title="自动排布" onClick={relayout}>
                    <LayoutGrid size={14} />
                  </button>
                </div>
                <div className="storyboard-hint">
                  镜序从左到右 · 同段落整行横排 · 左下「自动排布」可重置 · 底部 #01… 与卡片一致
                </div>
              </>
            )}
          </div>

          <aside className="timeline-inspector">
            <h3>镜头属性</h3>
            {!selected ? (
              <div className="inspector-empty" style={{ minHeight: 180 }}>
                <Film size={24} />
                <div>在画布或底部镜头条选择镜头</div>
              </div>
            ) : (
              <>
                <div className="tl-preview-box">
                  {selected.video_url ? (
                    <video
                      key={`v-${selected.storyboard_id}-${selected.video_url}-${mediaRev}`}
                      src={mediaUrl(selected.video_url, mediaRev > 0)}
                      poster={mediaUrl(selected.thumbnail, mediaRev > 0) || undefined}
                      controls
                      preload="metadata"
                    />
                  ) : selected.thumbnail ? (
                    <img
                      key={`t-${selected.storyboard_id}-${selected.thumbnail}-${mediaRev}`}
                      src={mediaUrl(selected.thumbnail, mediaRev > 0)}
                      alt="镜头预览"
                    />
                  ) : (
                    <div className="preview-empty">
                      <Play size={28} />
                      <span>无预览</span>
                    </div>
                  )}
                </div>
                <div className="preview-meta" style={{ padding: "4px 0 8px" }}>
                  <span>
                    镜头 {String(selected.index + 1).padStart(2, "0")}
                    {" · "}
                    本镜 {selected.duration.toFixed(1)}s
                  </span>
                  <span title="本镜起点 / 整集总时长">
                    {formatTime(selected.start)} / 总 {formatTime(timeline?.duration ?? 0)}
                    {videoClips.length ? ` · 共 ${videoClips.length} 镜` : ""}
                  </span>
                </div>
                {selected.segment_key && selected.segment_total && selected.segment_total >= 2 && (
                  <div
                    style={{
                      marginBottom: 8,
                      padding: "8px 10px",
                      borderRadius: 8,
                      background: "var(--panel2)",
                      border: "1px solid var(--green)",
                      fontSize: 11,
                      color: "var(--text2)",
                      lineHeight: 1.4,
                    }}
                  >
                    <div style={{ marginBottom: 6 }}>
                      <strong style={{ color: "var(--green-t)" }}>运镜段落批量出视频</strong>
                      {" · "}
                      {selected.segment_title || "连续镜头"}
                      {" · "}
                      {selected.segment_part}/{selected.segment_total}
                      <span style={{ color: "var(--text3)" }}>
                        {" "}
                        （共 {segmentIds(selected.segment_key).length} 镜）
                      </span>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      <button
                        type="button"
                        className="btn-secondary"
                        style={{ width: "100%" }}
                        disabled={videoBusy}
                        onClick={() => selectSegment(selected.segment_key)}
                      >
                        ① 选本段全部（{segmentIds(selected.segment_key).length} 镜）
                      </button>
                      <button
                        type="button"
                        className="btn-primary"
                        style={{ width: "100%" }}
                        disabled={videoBusy || selected.storyboard_id == null}
                        onClick={() => void batchGenerateSegmentVideos(selected.segment_key)}
                        title="按段落顺序批量定稿出片（MiniMax H3）"
                      >
                        <Film size={13} /> ② 本段批量定稿 · H3
                      </button>
                    </div>
                    <p style={{ margin: "6px 0 0", fontSize: 10, color: "var(--text3)", lineHeight: 1.4 }}>
                      单镜：卡片上「定本镜」。批量：点上面「本段批量定稿」，或先勾选再点底部「批量定稿」。
                      按 1→{selected.segment_total} 顺序生成，后镜接前镜尾帧（MiniMax H3 图生视频）。
                    </p>
                  </div>
                )}

                <label>时长（秒，AI 视频最长 5s）</label>
                <input
                  type="number"
                  min="1"
                  max="5"
                  step="1"
                  value={selected.duration}
                  onChange={(event) => updateDuration(Number(event.target.value))}
                />
                <label>字幕文本</label>
                <textarea
                  rows={4}
                  value={selectedSubtitle?.subtitle_text ?? selected.subtitle_text ?? ""}
                  onChange={(event) => updateSubtitle(event.target.value)}
                  placeholder="输入当前镜头字幕"
                />
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 5 }}>
                  <span style={{ flex: 1, fontSize: 10, color: "var(--text3)" }}>修改后自动保存</span>
                  <button
                    type="button"
                    className="btn-secondary"
                    style={{ width: "auto", padding: "3px 8px", fontSize: 10 }}
                    disabled={!(selectedSubtitle?.subtitle_text ?? selected.subtitle_text ?? "")}
                    onClick={() => updateSubtitle("")}
                  >
                    清空字幕
                  </button>
                </div>

                <label>说话角色（写入提示词，供模型生成语音）</label>
                <select
                  value={selected.storyboard_id != null ? (speakingChars[selected.storyboard_id] ?? "") : ""}
                  onChange={(event) => {
                    if (selected.storyboard_id != null)
                      void setSpeakingCharacter(selected.storyboard_id, event.target.value ? Number(event.target.value) : null);
                  }}
                >
                  <option value="">未指定</option>
                  {characters.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
                <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--text3)", lineHeight: 1.45 }}>
                  定稿出片：角色/场景多参考 + 台词栏「角色名：台词」由 H3 照念生成语音。无台词则闭嘴只出环境音，不会即兴乱说。
                </p>
                <div className="reorder-buttons" style={{ marginTop: 8 }}>
                  <button className="btn-secondary" disabled={selected.index === 0} onClick={() => reorder(-1)}>
                    <ChevronLeft size={14} /> 前移
                  </button>
                  <button
                    className="btn-secondary"
                    disabled={selected.index === videoClips.length - 1}
                    onClick={() => reorder(1)}
                  >
                    后移 <ChevronRight size={14} />
                  </button>
                </div>

                <label style={{ marginTop: 10 }}>视频提示词</label>
                <textarea
                  rows={3}
                  value={selected.storyboard_id != null ? videoPrompts[selected.storyboard_id] ?? "" : ""}
                  onChange={(event) => {
                    const sbId = selected.storyboard_id;
                    if (sbId != null) setVideoPrompts((prev) => ({ ...prev, [sbId]: event.target.value }));
                  }}
                  placeholder="留空＝用镜头图提示词"
                />
                <div style={{ marginTop: 10, fontSize: 10, lineHeight: 1.45, color: "var(--text3)" }}>
                  默认 <strong>3 秒</strong> · MiniMax H3 多参考 r2v。可在时长框改为 5 秒。批量走任务队列。
                </div>
                {selected.storyboard_id != null && (
                  <VideoVersionsPanel
                    storyboardId={selected.storyboard_id}
                    disabled={videoBusy}
                    onApplied={async () => {
                      await reloadTimeline();
                      setNotice("已切换时间线视频版本");
                    }}
                  />
                )}
                {selected.storyboard_id != null && (
                  <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                    <MediaUploadButton
                      targetType="storyboard_image"
                      targetId={selected.storyboard_id}
                      username={username}
                      accept="image/png,image/jpeg,image/webp,image/gif"
                      label=" 上传分镜图"
                      title="从本机上传本镜静帧"
                      disabled={videoBusy}
                      style={{ flex: 1, minWidth: 100 }}
                      onDone={async (r) => {
                        // 立即改当前选中镜头缩略图，再拉时间线（存库路径，展示走 mediaUrl）
                        if (timeline && selected.storyboard_id != null) {
                          const sid = selected.storyboard_id;
                          const thumb = r.image_url || r.url;
                          setTimeline({
                            ...timeline,
                            tracks: {
                              ...timeline.tracks,
                              video: {
                                ...timeline.tracks.video,
                                clips: timeline.tracks.video.clips.map((c) =>
                                  c.storyboard_id === sid
                                    ? { ...c, thumbnail: thumb, video_url: c.video_url }
                                    : c,
                                ),
                              },
                            },
                          });
                        }
                        setMediaRev((n) => n + 1);
                        await reloadTimeline();
                        setNoticeKind("normal");
                        setNotice("分镜图已上传并刷新");
                      }}
                      onError={(m) => {
                        setNoticeKind("error");
                        setNotice(m);
                      }}
                    />
                    <MediaUploadButton
                      targetType="storyboard_video"
                      targetId={selected.storyboard_id}
                      username={username}
                      accept="video/mp4,video/webm,video/quicktime"
                      label=" 上传视频"
                      title="从本机上传本镜成片视频"
                      disabled={videoBusy}
                      style={{ flex: 1, minWidth: 100 }}
                      onDone={async (r) => {
                        if (timeline && selected.storyboard_id != null) {
                          const sid = selected.storyboard_id;
                          const vurl = r.video_url || r.url;
                          const thumb = r.image_url || null;
                          setTimeline({
                            ...timeline,
                            tracks: {
                              ...timeline.tracks,
                              video: {
                                ...timeline.tracks.video,
                                clips: timeline.tracks.video.clips.map((c) =>
                                  c.storyboard_id === sid
                                    ? {
                                        ...c,
                                        video_url: vurl,
                                        // 有新抽帧则换海报；没有也清掉旧海报，避免仍显示上一版画面
                                        thumbnail: thumb || null,
                                      }
                                    : c,
                                ),
                              },
                            },
                          });
                        }
                        setMediaRev((n) => n + 1);
                        await reloadTimeline();
                        setMediaRev((n) => n + 1);
                        setNoticeKind("normal");
                        setNotice("分镜视频已上传并刷新，可导出合镜");
                      }}
                      onError={(m) => {
                        setNoticeKind("error");
                        setNotice(m);
                      }}
                    />
                  </div>
                )}
                {/* 出片参数总览 */}
                <div
                  style={{
                    marginTop: 10,
                    padding: "10px 12px",
                    borderRadius: 8,
                    background: "var(--surface)",
                    border: "1px solid var(--border)",
                    fontSize: 11,
                    lineHeight: 1.6,
                    color: "var(--text2)",
                  }}
                >
                  <div style={{ fontWeight: 600, marginBottom: 6, color: "var(--text1)", fontSize: 11.5 }}>
                    ⚙ 出片参数
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 10px" }}>
                    <span style={{ color: "var(--text3)" }}>模型</span>
                    <span>MiniMax H3 · 多参考图生视频（r2v）</span>
                    <span style={{ color: "var(--text3)" }}>时长</span>
                    <span>
                      <strong style={{ color: "var(--blue)" }}>{selected.duration.toFixed(0)} 秒</strong>
                      <span style={{ color: "var(--text3)" }}>（上方时长框可改 1–5s）</span>
                    </span>
                    <span style={{ color: "var(--text3)" }}>画幅</span>
                    <span>16:9 · 1024×576</span>
                    <span style={{ color: "var(--text3)" }}>参考图</span>
                    <span>{selected.thumbnail ? "本镜缩略图 + " : ""}角色/场景定妆（自动匹配）</span>
                    <span style={{ color: "var(--text3)" }}>提示词</span>
                    <span>
                      分镜提示词 → H3 编排优化
                      {selected.storyboard_id != null && (videoPrompts[selected.storyboard_id] ?? "").trim()
                        ? "（已手动覆写）"
                        : "（自动）"}
                    </span>
                    <span style={{ color: "var(--text3)" }}>语音</span>
                    <span>
                      {selectedSubtitle?.subtitle_text ?? selected.subtitle_text ?? ""
                        ? "台词写入提示词，模型生成语音"
                        : "无台词"}
                    </span>
                    <span style={{ color: "var(--text3)" }}>字幕</span>
                    <span style={{ color: "var(--green-t)" }}>已全局禁止 ✓</span>
                  </div>
                </div>

                <button
                  className="btn-primary"
                  style={{ marginTop: 8, width: "100%" }}
                  disabled={selected.storyboard_id == null || videoBusy}
                  onClick={() => void generateVideoForSelected()}
                  title="MiniMax H3 · 多参考图 r2v · 提示词语音 · 定稿成片"
                >
                  {generatingVideo?.id === selected.storyboard_id && generatingVideo.mode === "final" ? (
                    <LoaderCircle className="spin" size={14} />
                  ) : (
                    <Film size={14} />
                  )}
                  {generatingVideo?.id === selected.storyboard_id && generatingVideo.mode === "final"
                    ? " 生成中…"
                    : ` 定稿出片 · r2v · ${selected.duration.toFixed(0)}s`}
                </button>
                {!(selected.subtitle_text || "").trim() && (
                  <div style={{ marginTop: 5, fontSize: 10, lineHeight: 1.45, color: "var(--amber)" }}>
                    本镜台词为空 → 出片将无对白。要说话请先填「角色名：内容」。
                  </div>
                )}
                {!selected.thumbnail && (
                  <div style={{ marginTop: 5, fontSize: 10, lineHeight: 1.45, color: "var(--text3)" }}>
                    无镜头缩略图也可出片：使用角色/场景多参考图；可选上传补充参考。
                  </div>
                )}
              </>
            )}
          </aside>
        </div>

        {showTracks && timeline && (
          <section className="timeline-editor timeline-editor-compact">
            <div className="timeline-toolbar">
              <span>
                <Film size={14} /> 总时长 {formatTime(timeline.duration)}
              </span>
              <div />
              <span style={{ fontSize: 11, color: "var(--text3)" }}>多轨预览（可选）</span>
            </div>
            <div className="timeline-scroll">
              {TRACKS.map(({ name, label, color, icon: Icon }) => {
                const track = timeline.tracks[name];
                return (
                  <div className={`timeline-track${track.enabled ? "" : " muted"}`} key={name}>
                    <button
                      className="track-label"
                      onClick={() => toggleTrack(name)}
                      title={track.enabled ? "关闭轨道" : "开启轨道"}
                    >
                      <Icon size={14} />
                      <span>{label}</span>
                      <i className={track.enabled ? "on" : ""} />
                    </button>
                    <div className="track-lane">
                      {track.clips.map((clip, clipIndex) => (
                        <button
                          key={`${name}-${clip.storyboard_id ?? clipIndex}`}
                          className={`timeline-clip${clip.storyboard_id === selectedId ? " selected" : ""}`}
                          style={{ flexGrow: Math.max(clip.duration, 0.2), background: color }}
                          onClick={() => clip.storyboard_id != null && focusShot(clip.storyboard_id)}
                        >
                          <strong>
                            {name === "subtitle"
                              ? clip.subtitle_text || "空字幕"
                              : name === "voiceover"
                                ? characters.find((character) => character.id === speakingChars[clip.storyboard_id ?? -1])
                                    ?.name || `配音 ${clip.index + 1}`
                                : `片段 ${clip.index + 1}`}
                          </strong>
                          <span>{clip.duration.toFixed(1)}s</span>
                        </button>
                      ))}
                      {!track.clips.length && <span className="track-empty">暂无{label}素材</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {/* 底部镜头条 */}
        <div className="timeline-filmstrip">
          <div className="filmstrip-toolbar">
            <label className="filmstrip-check-all">
              <input
                type="checkbox"
                checked={clipIds.length > 0 && clipIds.every((id) => sel.selected.has(id))}
                onChange={() => {
                  if (clipIds.every((id) => sel.selected.has(id))) sel.clear();
                  else sel.replace(clipIds);
                }}
                disabled={!clipIds.length}
              />
              全选
            </label>
            <span className="filmstrip-count">
              {videoClips.length} 镜 · 已选 {sel.selected.size} · 总时长 {formatTime(timeline?.duration ?? 0)}
            </span>
            <div style={{ flex: 1 }} />
            {selected?.segment_key && (
              <button
                type="button"
                className="toolbar-button"
                disabled={videoBusy}
                onClick={() => selectSegment(selected.segment_key)}
                title="勾选当前镜头所属运镜段落的全部子镜"
              >
                选本段×{segmentIds(selected.segment_key).length || selected.segment_total || ""}
              </button>
            )}
            <button
              type="button"
              className="toolbar-button"
              disabled={(!sel.selected.size && !selected?.segment_key) || videoBusy}
              onClick={() => {
                if (sel.selected.size) void batchGenerateSelectedVideos();
                else if (selected?.segment_key) void batchGenerateSegmentVideos(selected.segment_key);
              }}
              title="批量定稿：先勾选，或未勾选时出当前段落全部（MiniMax H3）"
            >
              {batchRunning === "final" ? <LoaderCircle className="spin" size={14} /> : <Film size={14} />}
              {batchRunning === "final" ? " 入队中…" : " 批量定稿 · H3"}
            </button>
            <button
              type="button"
              className="toolbar-button"
              disabled={episodeId == null || videoBusy}
              title="检查图/视频完备性"
              onClick={() => {
                if (episodeId == null) return;
                void runProductionSupervise(episodeId).then((r) => {
                  setNotice(String(r.summary || "监督完成"));
                  setNoticeKind(r.grade === "A" ? "normal" : "error");
                }).catch((e) => {
                  setNoticeKind("error");
                  setNotice(e instanceof Error ? e.message : "监督失败");
                });
              }}
            >
              <AlertTriangle size={14} /> 生产监督
            </button>
            <button
              type="button"
              className="export-button"
              style={{ minWidth: 110, padding: "7px 12px" }}
              disabled={!videoClips.length || exporting || saving}
              onClick={() => void exportMovie(false)}
              title="同步导出；会应用各镜 trim 入出点"
            >
              {exporting ? <LoaderCircle className="spin" size={14} /> : <Film size={14} />}
              {exporting ? "导出中" : "导出成片"}
            </button>
            <button
              type="button"
              className="toolbar-button"
              style={{ minWidth: 90, padding: "7px 10px" }}
              disabled={!videoClips.length || exporting || saving}
              onClick={() => void exportMovie(true)}
              title="后台导出，可看任务队列"
            >
              后台导出
            </button>
          </div>
          <div className="filmstrip-scroll" ref={stripRef}>
            {!videoClips.length && !loading && (
              <div className="filmstrip-empty">暂无镜头 — 请先在「镜头脚本」拆列表（多图参考出片，不做出图分镜）</div>
            )}
            {videoClips.map((clip) => {
              if (clip.storyboard_id == null) return null;
              const id = clip.storyboard_id;
              const thumb = mediaUrl(clip.thumbnail);
              const active = selectedId === id;
              const picked = sel.selected.has(id);
              const hasVideo = Boolean(clip.video_url);
              return (
                <button
                  key={id}
                  type="button"
                  data-shot={id}
                  className={`filmstrip-shot${active ? " active" : ""}${picked ? " picked" : ""}`}
                  onClick={() => focusShot(id)}
                  title={clip.subtitle_text || `镜头 ${clip.index + 1}`}
                >
                  <span
                    className="filmstrip-shot-check"
                    data-no-drag
                    onClick={(e) => {
                      e.stopPropagation();
                      sel.toggle(id);
                    }}
                  >
                    <input type="checkbox" checked={picked} readOnly />
                  </span>
                  <span className="filmstrip-shot-media">
                    {thumb ? <img src={thumb} alt="" /> : hasVideo ? <Video size={18} /> : <ImageIcon size={18} />}
                    {hasVideo && <i className="filmstrip-video-dot" />}
                    {generatingVideo?.id === id && (
                      <span className="filmstrip-busy">
                        <LoaderCircle size={14} className="spin" />
                      </span>
                    )}
                  </span>
                  <span className="filmstrip-shot-label">#{clip.index + 1}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="timeline-export-bar">
        {mergedUrl ? (
          <div className="export-result">
            <Film size={15} />
            <span>成片已就绪</span>
            <a href={mergedUrl} target="_blank" rel="noreferrer">
              <Play size={14} /> 播放
            </a>
            <a href={mergedUrl} download>
              <Download size={14} /> 下载
            </a>
          </div>
        ) : (
          <span>导出时将按镜头顺序合成 MP4 · 底部镜头条 + 无限画布工作台</span>
        )}
      </div>
    </div>
  );
}

/** 多版视频列表：定稿/说话片择优上时间线 */
function VideoVersionsPanel({
  storyboardId,
  disabled,
  onApplied,
}: {
  storyboardId: number;
  disabled?: boolean;
  onApplied: () => void | Promise<void>;
}) {
  const [rows, setRows] = useState<{ video_id?: number; id?: number; model?: string | null; video_url?: string | null; quality_mode?: string | null; status?: string }[]>([]);
  const [trimIn, setTrimIn] = useState("");
  const [trimOut, setTrimOut] = useState("");

  useEffect(() => {
    let alive = true;
    listStoryboardVideos(storyboardId)
      .then((list) => {
        if (!alive) return;
        setRows(
          (list || []).map((v) => ({
            video_id: (v as { video_id?: number; id?: number }).video_id ?? (v as { id?: number }).id,
            id: (v as { id?: number }).id,
            model: v.model,
            video_url: v.video_url,
            quality_mode: (v as { quality_mode?: string }).quality_mode,
            status: v.status,
          })),
        );
      })
      .catch(() => alive && setRows([]));
    return () => {
      alive = false;
    };
  }, [storyboardId]);

  return (
    <div style={{ marginTop: 8, fontSize: 11 }}>
      <div style={{ color: "var(--text3)", marginBottom: 4 }}>历史版本（点选用到时间线）</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 120, overflow: "auto" }}>
        {!rows.length && <span style={{ color: "var(--text3)" }}>暂无历史版本</span>}
        {rows.map((r) => {
          const vid = r.video_id ?? r.id;
          if (vid == null) return null;
          return (
            <button
              key={vid}
              type="button"
              className="btn-secondary"
              style={{ fontSize: 11, justifyContent: "space-between", width: "100%" }}
              disabled={disabled || !r.video_url}
              onClick={() =>
                void selectVideoVersion(storyboardId, vid)
                  .then(onApplied)
                  .catch(() => undefined)
              }
            >
              <span>#{vid}</span>
              <span style={{ opacity: 0.8 }}>{r.quality_mode || r.model || r.status || "video"}</span>
            </button>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 6, marginTop: 6, alignItems: "center" }}>
        <input
          style={{ width: 56, fontSize: 11 }}
          placeholder="入点s"
          value={trimIn}
          onChange={(e) => setTrimIn(e.target.value)}
        />
        <input
          style={{ width: 56, fontSize: 11 }}
          placeholder="出点s"
          value={trimOut}
          onChange={(e) => setTrimOut(e.target.value)}
        />
        <button
          type="button"
          className="btn-secondary"
          style={{ fontSize: 11 }}
          disabled={disabled}
          onClick={() => {
            const a = trimIn === "" ? null : Number(trimIn);
            const b = trimOut === "" ? null : Number(trimOut);
            void setVideoTrim(storyboardId, a, b).then(onApplied);
          }}
        >
          保存裁切
        </button>
      </div>
    </div>
  );
}
