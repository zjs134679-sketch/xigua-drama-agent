/** 本地上传图片/视频，绑定到资产或分镜。 */
import { useRef, useState } from "react";
import { LoaderCircle, Upload } from "lucide-react";
import { mediaDisplayUrl, uploadMedia, type MediaUploadTarget } from "../api/client";

export default function MediaUploadButton({
  targetType,
  targetId,
  username = "local",
  accept,
  label,
  title,
  disabled,
  className,
  style,
  onDone,
  onError,
}: {
  targetType: MediaUploadTarget;
  targetId: number;
  username?: string;
  /** 如 image/* 或 video/* 或 image/*,video/* */
  accept: string;
  label?: string;
  title?: string;
  disabled?: boolean;
  className?: string;
  style?: React.CSSProperties;
  onDone: (result: {
    url: string;
    image_url?: string | null;
    video_url?: string | null;
    /** 带 /api 与缓存破坏的可展示地址 */
    display_url: string;
  }) => void | Promise<void>;
  onError?: (message: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);

  const pick = () => {
    if (disabled || busy) return;
    inputRef.current?.click();
  };

  const onChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy(true);
    try {
      const result = await uploadMedia({
        file,
        target_type: targetType,
        target_id: targetId,
        username,
      });
      const raw = result.image_url || result.video_url || result.url;
      if (!raw) {
        throw new Error("上传成功但未返回文件地址");
      }
      await onDone({
        url: result.url || raw,
        image_url: result.image_url || (targetType === "storyboard_video" ? null : raw),
        video_url: result.video_url,
        display_url: mediaDisplayUrl(raw, true),
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "上传失败";
      // 旧后端未加载 /assets/upload 时常见 404
      if (/404|Not Found/i.test(msg)) {
        onError?.("上传接口不可用（404）。请重启后端后再试。");
      } else {
        onError?.(msg);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        style={{ display: "none" }}
        onChange={(e) => void onChange(e)}
      />
      <button
        type="button"
        className={className ?? "btn-secondary"}
        style={style}
        disabled={disabled || busy}
        title={title || "从本机选择文件上传"}
        onClick={pick}
      >
        {busy ? <LoaderCircle className="spin" size={13} /> : <Upload size={13} />}
        {busy
          ? " 上传中…"
          : label ?? (accept.includes("video") && !accept.includes("image") ? " 上传视频" : " 上传图片")}
      </button>
    </>
  );
}
