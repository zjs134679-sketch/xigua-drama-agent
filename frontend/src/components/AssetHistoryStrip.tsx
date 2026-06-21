import { useEffect, useState } from "react";
import {
  listAssetHistory,
  useAssetHistory,
  type AssetHistoryItem,
  type AssetTargetType,
} from "../api/client";

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

  useEffect(() => {
    listAssetHistory(targetType, targetId).then(setItems).catch(() => setItems([]));
  }, [targetType, targetId, currentImageUrl]);

  if (!items.length) return null;

  const choose = async (item: AssetHistoryItem) => {
    setUsingId(item.id);
    try {
      const result = await useAssetHistory(item.id);
      onUse(result.image_url);
    } finally {
      setUsingId(null);
    }
  };

  return (
    <div style={{ display: "flex", gap: 5, padding: "6px 8px", borderTop: "1px solid var(--border)", background: "var(--panel2)" }} title="最近 3 版图片">
      {items.map((item) => {
        const source = item.image_url || item.local_path || "";
        const active = Boolean(currentImageUrl && (currentImageUrl === item.image_url || currentImageUrl === item.local_path));
        return (
          <button
            type="button"
            key={item.id}
            onClick={() => choose(item)}
            disabled={usingId != null}
            title="选用这一版"
            style={{ width: 42, height: 32, padding: 0, overflow: "hidden", borderRadius: 4, border: active ? "2px solid var(--green)" : "1px solid var(--border2)", background: "var(--surface)", opacity: usingId === item.id ? 0.6 : 1 }}
          >
            {source && <img src={source} alt="历史版本" style={{ width: "100%", height: "100%", objectFit: "cover" }} />}
          </button>
        );
      })}
    </div>
  );
}
