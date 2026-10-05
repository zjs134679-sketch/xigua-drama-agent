/** 当前分集生产流水线进度条 —— 引导下一步（西瓜原创，无第三方内容）。 */
import { useCallback, useEffect, useState } from "react";
import {
  Check,
  Circle,
  FileText,
  Film,
  LayoutGrid,
  Mountain,
  Palette,
  Users,
} from "lucide-react";
import {
  listCharacters,
  listScenes,
  listStoryboards,
  type EpisodeSummary,
  type Project,
  type Storyboard,
} from "../api/client";
import { projectStyleName } from "../features/styleHelpers";

export type PipelineViewId =
  | "project"
  | "script"
  | "art-styles"
  | "characters"
  | "scenes"
  | "storyboard"
  | "timeline";

/** 资产台出图/上传后派发，流水线立即刷新计数 */
export const ASSETS_CHANGED_EVENT = "xigua:assets-changed";

export function notifyAssetsChanged() {
  try {
    window.dispatchEvent(new CustomEvent(ASSETS_CHANGED_EVENT));
  } catch {
    /* ignore */
  }
}

type Step = {
  id: PipelineViewId;
  label: string;
  icon: typeof FileText;
  done: boolean;
  hint: string;
};

export default function PipelineBar({
  drama,
  episode,
  currentView,
  onGoto,
}: {
  drama: Project | null;
  episode: EpisodeSummary | null;
  /** 当前主视图，用于判断「下一步」是否已在本页 */
  currentView?: string | null;
  onGoto: (view: PipelineViewId) => void;
}) {
  const [shots, setShots] = useState<Storyboard[]>([]);
  const [charOk, setCharOk] = useState(0);
  const [charTotal, setCharTotal] = useState(0);
  const [sceneOk, setSceneOk] = useState(0);
  const [sceneTotal, setSceneTotal] = useState(0);
  const [tick, setTick] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const hasImage = (url: string | null | undefined) => Boolean(url && String(url).trim());

  const reload = useCallback(async () => {
    if (!drama?.id || !episode?.id) {
      setShots([]);
      setCharOk(0);
      setCharTotal(0);
      setSceneOk(0);
      setSceneTotal(0);
      return;
    }
    setRefreshing(true);
    try {
      // 流水线按「本集」统计：避免全剧龙套/未用场景拖成永不绿
      const [sb, chars, scenes] = await Promise.all([
        listStoryboards(episode.id).catch(() => [] as Storyboard[]),
        listCharacters(drama.id, episode.id).catch(() => []),
        listScenes(drama.id, episode.id).catch(() => []),
      ]);
      setShots(sb);
      setCharTotal(chars.length);
      setCharOk(chars.filter((c) => hasImage(c.image_url)).length);
      setSceneTotal(scenes.length);
      setSceneOk(scenes.filter((s) => hasImage(s.image_url)).length);
    } finally {
      setRefreshing(false);
    }
  }, [drama?.id, episode?.id]);

  useEffect(() => {
    void reload();
  }, [reload, tick]);

  // 定时刷新 + 窗口回前台 + 资产变更事件（修「出图后仍 3/5」）
  useEffect(() => {
    if (!drama?.id || !episode?.id) return;
    const interval = window.setInterval(() => setTick((n) => n + 1), 3000);
    const onFocus = () => setTick((n) => n + 1);
    const onAssets = () => setTick((n) => n + 1);
    window.addEventListener("focus", onFocus);
    window.addEventListener(ASSETS_CHANGED_EVENT, onAssets);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener(ASSETS_CHANGED_EVENT, onAssets);
    };
  }, [drama?.id, episode?.id]);

  if (!drama || !episode) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "6px 12px",
          borderBottom: "1px solid var(--border)",
          background: "var(--panel2)",
          fontSize: 12,
          color: "var(--text3)",
        }}
      >
        <span>生产流水线</span>
        <span style={{ opacity: 0.8 }}>请先在「项目」选择短剧与分集</span>
        <button
          type="button"
          className="btn-secondary"
          style={{ width: "auto", padding: "2px 10px", marginLeft: "auto" }}
          onClick={() => onGoto("project")}
        >
          去项目
        </button>
      </div>
    );
  }

  const hasStyle = Boolean(projectStyleName(drama) || drama.style_bible?.art_style_id);
  const hasScript = Boolean(episode.has_script);
  const hasShots = shots.length > 0;
  const videoed = shots.filter((s) => s.video_url).length;

  // 本集相关资产全部出图才标绿；0 条=本集尚未提取/未关联（不绿）
  const charsDone = charTotal > 0 && charOk === charTotal;
  const scenesDone = sceneTotal > 0 && sceneOk === sceneTotal;
  // 分镜：有镜头即可绿（不再要求分镜静帧出图；台词/提示词在分镜台编辑）
  const shotsDone = hasShots;

  const steps: Step[] = [
    { id: "project", label: "画风", icon: Palette, done: hasStyle, hint: "绑定项目画风" },
    { id: "script", label: "剧本", icon: FileText, done: hasScript, hint: "生成/粘贴剧本" },
    {
      id: "characters",
      label: "角色图",
      icon: Users,
      done: charsDone,
      hint: charTotal ? `本集 ${charOk}/${charTotal}` : "本集提取后出图",
    },
    {
      id: "scenes",
      label: "场景图",
      icon: Mountain,
      done: scenesDone,
      hint: sceneTotal ? `本集 ${sceneOk}/${sceneTotal}` : "本集提取后出图",
    },
    {
      id: "storyboard",
      label: "镜头脚本",
      icon: LayoutGrid,
      done: shotsDone,
      hint: hasShots ? `${shots.length} 镜` : "拆列表+提示词/台词",
    },
    {
      id: "timeline",
      label: "成片",
      icon: Film,
      done: hasShots && videoed > 0,
      hint: hasShots ? `${videoed}/${shots.length} 段视频` : "多图参考生视频",
    },
  ];

  const next = steps.find((s) => !s.done);
  const onNextPageAlready = Boolean(next && currentView && next.id === currentView);
  // 已有部分场景/角色图时，允许直接去成片（不必卡死在「下一步场景图」）
  const canSkipToTimeline =
    hasScript && (charOk > 0 || sceneOk > 0) && next && next.id !== "timeline";

  const handleNext = () => {
    if (!next) return;
    if (onNextPageAlready) {
      // 已在目标页：刷新进度，避免「点了没反应」
      void reload();
      return;
    }
    onGoto(next.id);
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: "6px 12px",
        borderBottom: "1px solid var(--border)",
        background: "var(--panel2)",
        fontSize: 11,
        flexWrap: "wrap",
      }}
    >
      <span style={{ color: "var(--text3)", marginRight: 4 }}>流水线</span>
      {steps.map((step, i) => {
        const Icon = step.icon;
        return (
          <button
            key={step.id}
            type="button"
            onClick={() => onGoto(step.id)}
            title={step.hint}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "3px 8px",
              borderRadius: 999,
              border: `1px solid ${step.done ? "var(--green)" : "var(--border2)"}`,
              background: step.done ? "rgba(43,178,76,0.12)" : "var(--panel)",
              color: step.done ? "var(--green-t)" : "var(--text2)",
              cursor: "pointer",
              font: "inherit",
            }}
          >
            {step.done ? <Check size={12} /> : <Circle size={10} />}
            <Icon size={12} />
            {step.label}
            {!step.done && step.hint ? <span style={{ opacity: 0.75 }}>{step.hint}</span> : null}
            {i < steps.length - 1 ? <span style={{ opacity: 0.35, marginLeft: 2 }}>→</span> : null}
          </button>
        );
      })}
      <div style={{ flex: 1 }} />
      {next ? (
        <div style={{ display: "inline-flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn-primary"
            style={{ width: "auto", padding: "4px 12px", fontSize: 12, opacity: refreshing ? 0.75 : 1 }}
            onClick={handleNext}
            title={
              onNextPageAlready
                ? "已在本页：点击刷新进度。全部出齐后步骤会变绿。"
                : `前往：${next.label}`
            }
          >
            {onNextPageAlready
              ? refreshing
                ? "刷新中…"
                : `刷新进度 · ${next.label}`
              : `下一步：${next.label}`}
          </button>
          {canSkipToTimeline ? (
            <button
              type="button"
              className="btn-secondary"
              style={{ width: "auto", padding: "4px 12px", fontSize: 12 }}
              onClick={() => onGoto("timeline")}
              title="已有部分资产图即可去成片；未出齐的场景/角色可后补"
            >
              去成片
            </button>
          ) : null}
        </div>
      ) : (
        <span style={{ color: "var(--green-t)" }}>
          本集关键步骤已齐
          {charTotal + sceneTotal > 0 && (charOk < charTotal || sceneOk < sceneTotal)
            ? "（可再补资产图）"
            : ""}
        </span>
      )}
    </div>
  );
}
