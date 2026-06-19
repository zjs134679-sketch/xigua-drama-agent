import { Ban } from "lucide-react";

export default function BanScreen({ reason }: { reason?: string }) {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        background: "rgba(10,11,13,0.96)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 14,
        zIndex: 50,
        textAlign: "center",
        padding: 24,
      }}
    >
      <Ban size={56} color="var(--red-t)" />
      <h1 style={{ fontSize: 22, fontWeight: 500, margin: 0, color: "var(--text)" }}>账号已封禁</h1>
      <p style={{ color: "var(--text2)", maxWidth: 360, lineHeight: 1.7, margin: 0 }}>
        {reason || "因多次触发内容红线，您的账号已被封禁。"}
        <br />
        如有疑问请联系客服申诉。
      </p>
    </div>
  );
}
