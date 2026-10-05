import type { CSSProperties } from "react";
import {
  ASSET_RESOLUTIONS,
  CHARACTER_ASSET_RESOLUTIONS,
  PROP_ASSET_RESOLUTIONS,
  SCENE_ASSET_RESOLUTIONS,
  type AssetResolution,
  type ComputeNodeRecord,
} from "../api/client";

/** 角色/场景定妆可选步数（H3 Turbo 必须 4；更高会与 Turbo LoRA 冲突） */
export const CHARACTER_STEPS_OPTIONS = [
  { value: 4, label: "4 步 · Turbo 推荐" },
  { value: 8, label: "8 步 · 仅无 Turbo 时" },
  { value: 20, label: "20 步 · 无 Turbo 较清" },
] as const;

/** 与角色共用同一套步数选项 */
export const SCENE_STEPS_OPTIONS = CHARACTER_STEPS_OPTIONS;

export default function AssetGenerationControls({
  resolution,
  nodeId,
  nodes,
  disabled,
  onResolutionChange,
  onNodeChange,
  /** 角色/场景：采样步数 */
  steps,
  onStepsChange,
  variant = "asset",
}: {
  resolution: AssetResolution;
  nodeId?: number;
  nodes: ComputeNodeRecord[];
  disabled?: boolean;
  onResolutionChange: (value: AssetResolution) => void;
  onNodeChange: (value: number | undefined) => void;
  steps?: number;
  onStepsChange?: (value: number) => void;
  /** character/scene/prop 可调步数；asset 兼容旧用法；video 仅占位 */
  variant?: "character" | "scene" | "prop" | "asset" | "video";
}) {
  const showSteps =
    (variant === "character" || variant === "scene" || variant === "prop") &&
    typeof onStepsChange === "function";

  const resolutionOptions =
    variant === "character"
      ? CHARACTER_ASSET_RESOLUTIONS
      : variant === "scene"
        ? SCENE_ASSET_RESOLUTIONS
        : variant === "prop"
          ? PROP_ASSET_RESOLUTIONS
          : ASSET_RESOLUTIONS;

  // 当前值若不在列表中（历史数据），并入下拉避免空白
  const options =
    resolutionOptions.some((o) => o.value === resolution)
      ? resolutionOptions
      : [
          ...resolutionOptions,
          ...(ASSET_RESOLUTIONS.filter((o) => o.value === resolution) as typeof resolutionOptions),
        ];

  const tip =
    variant === "character"
      ? "角色出图：H3 Turbo 请用 4 步。更清晰优先提高分辨率（竖图≤768×1344），不要盲目加步数。"
      : variant === "scene"
        ? "场景出图：H3 Turbo 请用 4 步。更清晰优先提高分辨率。"
        : variant === "prop"
          ? "道具出图：H3 Turbo 请用 4 步。更清晰优先提高分辨率。"
          : null;

  const fieldStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 2,
    minWidth: 0,
  };
  const labelStyle: CSSProperties = {
    fontSize: 10,
    color: "var(--text3)",
    lineHeight: 1.2,
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {tip ? (
        <span style={{ fontSize: 10, color: "var(--text3)", lineHeight: 1.4 }}>{tip}</span>
      ) : null}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: showSteps ? "1.2fr 1fr 1fr" : "1fr 1fr",
          gap: 6,
        }}
      >
        <label style={fieldStyle}>
          <span style={labelStyle}>分辨率</span>
          <select
            value={resolution}
            disabled={disabled}
            onChange={(event) => onResolutionChange(event.target.value as AssetResolution)}
            title={
              variant === "scene"
                ? "场景底板图分辨率"
                : variant === "prop"
                  ? "道具定妆图分辨率"
                  : "出图分辨率"
            }
          >
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        {showSteps ? (
          <label style={fieldStyle}>
            <span style={labelStyle}>采样步数</span>
            <select
              value={steps ?? 4}
              disabled={disabled}
              onChange={(event) => onStepsChange?.(Number(event.target.value))}
              title={
                variant === "scene"
                  ? "场景采样步数（Turbo 推荐 4）"
                  : variant === "prop"
                    ? "道具采样步数（Turbo 推荐 4）"
                    : "角色采样步数（Turbo 推荐 4）"
              }
            >
              {CHARACTER_STEPS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <label style={fieldStyle}>
          <span style={labelStyle}>算力节点</span>
          <select
            value={nodeId ?? ""}
            disabled={disabled}
            onChange={(event) => onNodeChange(event.target.value ? Number(event.target.value) : undefined)}
            title="本次出图算力节点（Comfy）"
          >
            <option value="">自动选择模型</option>
            {nodes.map((node) => (
              <option key={node.id} value={node.id} disabled={!node.is_active}>
                {node.name} · {node.type}
                {node.is_active ? "" : "（停用）"}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  );
}
