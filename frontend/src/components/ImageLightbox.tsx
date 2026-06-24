import { useEffect } from "react";
import { X } from "lucide-react";

/**
 * 全屏图片放大预览。点击素材缩略图时打开；点遮罩 / 关闭按钮 / Esc 关闭。
 * src 为 null 时不渲染。
 */
export default function ImageLightbox({
  src,
  alt,
  onClose,
}: {
  src: string | null;
  alt?: string;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!src) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [src, onClose]);

  if (!src) return null;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "rgba(0,0,0,0.82)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 32,
        cursor: "zoom-out",
      }}
    >
      <button
        type="button"
        onClick={onClose}
        title="关闭（Esc）"
        style={{
          position: "absolute",
          top: 16,
          right: 16,
          width: 36,
          height: 36,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: 8,
          border: "1px solid var(--border2)",
          background: "var(--panel)",
          color: "var(--text)",
          cursor: "pointer",
        }}
      >
        <X size={18} />
      </button>
      <img
        src={src}
        alt={alt ?? "预览"}
        onClick={(e) => e.stopPropagation()}
        style={{
          maxWidth: "100%",
          maxHeight: "100%",
          objectFit: "contain",
          borderRadius: 8,
          boxShadow: "0 8px 40px rgba(0,0,0,0.6)",
          cursor: "default",
        }}
      />
    </div>
  );
}
