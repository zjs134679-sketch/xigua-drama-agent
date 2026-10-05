import { CheckCheck, FlipHorizontal2, Images, ListChecks, StopCircle, X } from "lucide-react";

const chip: React.CSSProperties = {
  flex: "none",
  fontSize: 11,
  padding: "4px 9px",
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
};

/** 素材台批量工具条：快捷多选 + 批量生成 + 进度/停止。 */
export default function BatchBar({
  total,
  ungeneratedCount,
  selectedCount,
  onSelectAll,
  onSelectUngenerated,
  onSelectSegment,
  segmentCount,
  onInvert,
  onClear,
  onRun,
  onStop,
  running,
  progressText,
  runLabel = "批量出图",
}: {
  total: number;
  ungeneratedCount: number;
  selectedCount: number;
  onSelectAll: () => void;
  onSelectUngenerated: () => void;
  /** 选中当前检视镜头所在运镜段落（如 5 段） */
  onSelectSegment?: () => void;
  segmentCount?: number;
  onInvert: () => void;
  onClear: () => void;
  onRun: () => void;
  onStop: () => void;
  running: boolean;
  progressText: string | null;
  runLabel?: string;
}) {
  if (!total) return null;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        flexWrap: "wrap",
        padding: "6px 16px",
        borderBottom: "1px solid var(--border)",
        background: "var(--panel2)",
      }}
    >
      <button className="btn-secondary" style={chip} onClick={onSelectAll} disabled={running} title="选中当前全部素材">
        <CheckCheck size={13} /> 全选（{total}）
      </button>
      <button
        className="btn-secondary"
        style={chip}
        onClick={onSelectUngenerated}
        disabled={running || !ungeneratedCount}
        title="只选中还没出图的素材"
      >
        <ListChecks size={13} /> 未生成（{ungeneratedCount}）
      </button>
      {onSelectSegment && (
        <button
          className="btn-secondary"
          style={chip}
          onClick={onSelectSegment}
          disabled={running || !segmentCount}
          title="勾选当前镜头所属运镜段落的全部子镜"
        >
          <ListChecks size={13} /> 选本段落{segmentCount ? `（${segmentCount}）` : ""}
        </button>
      )}
      <button className="btn-secondary" style={chip} onClick={onInvert} disabled={running} title="反向选择">
        <FlipHorizontal2 size={13} /> 反选
      </button>
      <button className="btn-secondary" style={chip} onClick={onClear} disabled={running || !selectedCount} title="清空选择">
        <X size={13} /> 取消
      </button>
      <span style={{ fontSize: 11, color: "var(--text3)" }}>已选 {selectedCount}</span>
      <div style={{ flex: 1, minWidth: 12 }} />
      {progressText && <span style={{ fontSize: 11, color: "var(--text2)" }}>{progressText}</span>}
      {running ? (
        <button className="btn-secondary" style={{ ...chip, padding: "5px 12px" }} onClick={onStop} title="停止批量生成">
          <StopCircle size={13} /> 停止
        </button>
      ) : (
        <button
          className="btn-primary"
          style={{ width: "auto", padding: "6px 14px", opacity: selectedCount ? 1 : 0.5 }}
          onClick={onRun}
          disabled={!selectedCount}
        >
          <Images size={14} /> {runLabel}
          {selectedCount ? `（${selectedCount}）` : ""}
        </button>
      )}
    </div>
  );
}
