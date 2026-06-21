import {
  ASSET_RESOLUTIONS,
  type AssetResolution,
  type ComputeNodeRecord,
} from "../api/client";

export default function AssetGenerationControls({
  resolution,
  nodeId,
  nodes,
  disabled,
  onResolutionChange,
  onNodeChange,
}: {
  resolution: AssetResolution;
  nodeId?: number;
  nodes: ComputeNodeRecord[];
  disabled?: boolean;
  onResolutionChange: (value: AssetResolution) => void;
  onNodeChange: (value: number | undefined) => void;
}) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
      <select
        value={resolution}
        disabled={disabled}
        onChange={(event) => onResolutionChange(event.target.value as AssetResolution)}
        title="本次出图分辨率"
      >
        {ASSET_RESOLUTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
      <select
        value={nodeId ?? ""}
        disabled={disabled}
        onChange={(event) => onNodeChange(event.target.value ? Number(event.target.value) : undefined)}
        title="本次出图模型（算力节点）"
      >
        <option value="">自动选择模型</option>
        {nodes.map((node) => (
          <option key={node.id} value={node.id} disabled={!node.is_active}>
            {node.name} · {node.type}{node.is_active ? "" : "（停用）"}
          </option>
        ))}
      </select>
    </div>
  );
}
