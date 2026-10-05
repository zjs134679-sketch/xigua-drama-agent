import { useEffect, useState } from "react";
import {
  listAssetHistory,
  mediaDisplayUrl,
  useAssetHistory,
  type AssetHistoryItem,
  type AssetTargetType,
} from "../api/client";

/** 统一比较 image_url / local_path / /oss/ 文件名 */
function sameImage(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  if (a === b) return true;
  const norm = (v: string) => {
    const s = v.replace(/\\/g, "/").trim().split("?")[0];
    const name = s.split("/").pop() || s;
    return name.toLowerCase();
  };
  return norm(a) === norm(b);
}

function displaySrc(item: AssetHistoryItem): string {
  const raw = item.image_url || item.local_path || "";
  if (!raw) return "";
  return mediaDisplayUrl(raw);
}

export default function AssetHistoryStrip({
  targetType,
  targetId,
  currentImageUrl,
  onUse,
}: {
  targetType: AssetTargetType;
  targetId: number;
  currentImageUrl: string | null;
  onUse: (imageUrl: string | null) => void;
}) {
  const [items, setItems] = useState<AssetHistoryItem[]>([]);
  const [usingId, setUsingId] = useState<number | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setError("");
    listAssetHistory(targetType, targetId)
      .then(setItems)
      .catch(() => setItems([]));
  }, [targetType, targetId, currentImageUrl]);

  if (!items.length) return null;

  const choose = async (item: AssetHistoryItem) => {
    if (usingId != null) return;
    // 已是当前图：仍允许再点，避免「看起来选不上」
    setUsingId(item.id);
    setError("");
    try {
      const result = await useAssetHistory(item.id);
      const url = result.image_url || result.local_path || displaySrc(item) || null;
      onUse(url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "切换历史图失败");
    } finally {
      setUsingId(null);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        padding: "6px 8px",
        borderTop: "1px solid var(--border)",
        background: "var(--panel2)",
      }}
      title="最近 3 版图片，点击切换为当前图"
    >
      <div style={{ display: "flex", gap: 5, alignItems: "center" }}>
        <span style={{ fontSize: 10, color: "var(--text3)", marginRight: 2 }}>历史</span>
        {items.map((item) => {
          const source = displaySrc(item);
          const active = sameImage(currentImageUrl, item.image_url)
            || sameImage(currentImageUrl, item.local_path)
            || sameImage(currentImageUrl, source);
          const busy = usingId === item.id;
          return (
            <button
              type="button"
              key={item.id}
              onClick={() => void choose(item)}
              disabled={usingId != null && usingId !== item.id}
              title={active ? "当前使用中" : "选用这一版"}
              style={{
                width: 42,
                height: 32,
                padding: 0,
                overflow: "hidden",
                borderRadius: 4,
                border: active ? "2px solid var(--green)" : "1px solid var(--border2)",
                background: "var(--surface)",
                opacity: busy ? 0.6 : 1,
                cursor: usingId != null && usingId !== item.id ? "wait" : "pointer",
                flex: "none",
              }}
            >
              {source ? (
                <img src={source} alt="历史版本" style={{ width: "100%", height: "100%", objectFit: "cover", pointerEvents: "none" }} />
              ) : (
                <span style={{ fontSize: 9, color: "var(--text3)" }}>?</span>
              )}
            </button>
          );
        })}
      </div>
      {error && <div style={{ fontSize: 10, color: "var(--red, #e07070)" }}>{error}</div>}
    </div>
  );
}
