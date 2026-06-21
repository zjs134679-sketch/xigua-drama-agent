export default function AdditionalInstructionField({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 16px", borderBottom: "1px solid var(--border)", background: "var(--panel)" }}>
      <label style={{ flex: "none", fontSize: 11, color: "var(--text2)" }}>附加指令（追加到本页全部提示词）</label>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        placeholder="仅用于本次单个或批量出图，不保存到素材提示词"
        style={{ flex: 1, minWidth: 180 }}
      />
    </div>
  );
}
