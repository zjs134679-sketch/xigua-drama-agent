import { useEffect, useState } from "react";
import { AlertTriangle, Image as ImageIcon, Layers, Loader2, Wand2 } from "lucide-react";
import {
  generateStoryboardImage,
  generateStoryboards,
  listStoryboards,
  type ComplianceHit,
  type EpisodeSummary,
  type Project,
  type Storyboard,
} from "../api/client";

export default function StoryboardView({
  current,
  username,
  onBanned,
}: {
  current: { drama: Project; episode: EpisodeSummary } | null;
  username: string;
  onBanned?: () => void;
}) {
  const episode = current?.episode ?? null;
  const [shots, setShots] = useState<Storyboard[]>([]);
  const [loading, setLoading] = useState(false);
  const [breaking, setBreaking] = useState(false);
  const [imaging, setImaging] = useState<number | null>(null);
  const [err, setErr] = useState<{ message?: string; hits?: ComplianceHit[]; level?: string } | null>(null);

  const load = async (epId: number) => {
    setLoading(true);
    try {
      setShots(await listStoryboards(epId));
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (episode) load(episode.id);
    else setShots([]);
    setErr(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [episode?.id]);

  const breakdown = async () => {
    if (!episode) return;
    setBreaking(true);
    setErr(null);
    try {
      const res = await generateStoryboards(episode.id, username);
      if (res.status === 200 && res.storyboards) {
        setShots(res.storyboards);
      } else {
        setErr({ message: res.message, hits: res.hits, level: res.level });
        if (res.banned) onBanned?.();
      }
    } catch {
      setErr({ message: "请求失败，后端是否在运行？" });
    } finally {
      setBreaking(false);
    }
  };

  const makeImage = async (sb: Storyboard) => {
    setImaging(sb.id);
    setErr(null);
    try {
      const res = await generateStoryboardImage(sb.id, username);
      if (res.status === 200 && res.image_url) {
        setShots((prev) => prev.map((s) => (s.id === sb.id ? { ...s, image_url: res.image_url ?? null, status: "image_done" } : s)));
      } else {
        setErr({ message: res.message, hits: res.hits, level: res.level });
        if (res.banned) onBanned?.();
      }
    } catch {
      setErr({ message: "出图请求失败" });
    } finally {
      setImaging(null);
    }
  };

  if (!episode) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text3)", fontSize: 13 }}>
        请先在「项目」里选择一个分集，再来生成分镜。
      </div>
    );
  }

  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
        <Layers size={15} color="var(--text2)" />
        <span style={{ fontSize: 12, fontWeight: 500 }}>分镜台</span>
        <span style={{ fontSize: 11, color: "var(--text3)" }}>
          {current?.drama.title} · 第{episode.episode_number}集 · {shots.length} 个镜头
        </span>
        <div style={{ flex: 1 }} />
        <button
          className="btn-primary"
          style={{ width: "auto", padding: "6px 14px", opacity: breaking ? 0.7 : 1 }}
          onClick={breakdown}
          disabled={breaking}
          title="用编剧剧本重新拆解分镜（会覆盖现有分镜）"
        >
          {breaking ? <Loader2 size={14} className="spin" /> : <Wand2 size={14} />} {shots.length ? "重拆分镜" : "一键生成分镜"}
        </button>
      </div>

      {err && (
        <div style={{ margin: "8px 12px 0", padding: "8px 10px", borderRadius: 6, border: `1px solid ${err.level === "red" ? "var(--red)" : "var(--amber)"}`, color: err.level === "red" ? "var(--red-t)" : "var(--amber)", fontSize: 12 }}>
          <div style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 500 }}>
            <AlertTriangle size={14} /> {err.level === "red" ? "红线拦截" : "提示"}
          </div>
          <div style={{ marginTop: 4, color: "var(--text2)" }}>{err.message}</div>
          {err.hits && err.hits.length > 0 && (
            <div style={{ marginTop: 4, color: "var(--text2)" }}>命中：{err.hits.map((h) => `${h.word}(${h.category})`).join("、")}</div>
          )}
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: 12 }}>
        {loading ? (
          <div style={{ color: "var(--text3)", fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}><Loader2 size={14} className="spin" /> 加载分镜…</div>
        ) : shots.length === 0 ? (
          <div style={{ color: "var(--text3)", fontSize: 13, padding: "24px 0", textAlign: "center" }}>
            还没有分镜。点右上「一键生成分镜」，编剧剧本会被拆成镜头清单。
            <div style={{ fontSize: 11, marginTop: 6 }}>（需要该分集已生成剧本）</div>
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 12 }}>
            {shots.map((s) => (
              <div key={s.id} className="card" style={{ display: "flex", flexDirection: "column" }}>
                <div style={{ position: "relative", aspectRatio: "16/9", background: "var(--surface)", display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden" }}>
                  {s.image_url ? (
                    <img src={s.image_url} alt={s.title ?? ""} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                  ) : (
                    <ImageIcon size={24} color="var(--text3)" />
                  )}
                  <span style={{ position: "absolute", left: 6, top: 6, fontSize: 11, padding: "1px 6px", borderRadius: 4, background: "var(--bg)", color: "var(--text2)" }}>
                    镜头 {String(s.storyboard_number).padStart(2, "0")}
                  </span>
                </div>
                <div style={{ padding: "8px 10px", display: "flex", flexDirection: "column", gap: 5, flex: 1 }}>
                  <div style={{ fontSize: 12, fontWeight: 500 }}>{s.title || `镜头 ${s.storyboard_number}`}</div>
                  <div style={{ fontSize: 11, color: "var(--text3)" }}>
                    {[s.shot_type, s.location, s.duration ? `${s.duration}s` : null].filter(Boolean).join(" · ")}
                  </div>
                  {s.action && <div style={{ fontSize: 11, color: "var(--text2)", lineHeight: 1.4 }}>{s.action}</div>}
                  <div style={{ marginTop: "auto" }}>
                    <button
                      className="btn-secondary"
                      style={{ width: "100%", padding: "5px 10px", opacity: imaging === s.id ? 0.7 : 1 }}
                      onClick={() => makeImage(s)}
                      disabled={imaging === s.id}
                    >
                      {imaging === s.id ? <Loader2 size={13} className="spin" /> : <ImageIcon size={13} />} {s.image_url ? "重新出图" : "出图"}
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
