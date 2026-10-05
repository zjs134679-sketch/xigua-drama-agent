import { useEffect, useState } from "react";
import { ListTodo, LoaderCircle, X, XCircle } from "lucide-react";
import { cancelJob, listJobs, type ProductionJob } from "../api/client";

export default function TaskDrawer({
  open,
  onClose,
  dramaId,
}: {
  open: boolean;
  onClose: () => void;
  dramaId?: number | null;
}) {
  const [jobs, setJobs] = useState<ProductionJob[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = () => {
    setLoading(true);
    listJobs({ drama_id: dramaId ?? undefined, limit: 40 })
      .then((r) => setJobs(r.jobs || []))
      .catch(() => setJobs([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (!open) return;
    refresh();
    const t = window.setInterval(refresh, 2500);
    return () => window.clearInterval(t);
  }, [open, dramaId]);

  if (!open) return null;

  const active = jobs.filter((j) => j.status === "pending" || j.status === "running").length;

  return (
    <div
      style={{
        position: "fixed",
        right: 12,
        bottom: 48,
        width: 360,
        maxHeight: "55vh",
        zIndex: 80,
        background: "var(--panel)",
        border: "1px solid var(--border)",
        borderRadius: 10,
        boxShadow: "0 12px 40px rgba(0,0,0,0.45)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 12px", borderBottom: "1px solid var(--border)" }}>
        <ListTodo size={15} />
        <strong style={{ fontSize: 13 }}>生产任务</strong>
        <span style={{ fontSize: 11, color: "var(--text3)" }}>{active} 进行中</span>
        <div style={{ flex: 1 }} />
        {loading && <LoaderCircle size={13} className="spin" />}
        <button type="button" className="rail-btn" onClick={onClose} title="关闭">
          <X size={14} />
        </button>
      </div>
      <div style={{ overflow: "auto", padding: 8, fontSize: 12 }}>
        {!jobs.length && <div style={{ color: "var(--text3)", padding: 12 }}>暂无任务。批量出视频将进入队列。</div>}
        {jobs.map((j) => (
          <div
            key={j.id}
            style={{
              border: "1px solid var(--border2)",
              borderRadius: 8,
              padding: "8px 10px",
              marginBottom: 6,
              background: "var(--panel2)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <span>
                #{j.id} · {j.job_type}
              </span>
              <span
                style={{
                  color:
                    j.status === "completed"
                      ? "var(--green)"
                      : j.status === "failed"
                        ? "var(--red, #e55)"
                        : "var(--amber)",
                }}
              >
                {j.status}
              </span>
            </div>
            <div style={{ color: "var(--text3)", marginTop: 4 }}>{j.message || j.error_msg || "—"}</div>
            {(j.status === "running" || j.status === "pending") && (
              <div style={{ marginTop: 6, height: 4, background: "var(--border)", borderRadius: 2 }}>
                <div
                  style={{
                    width: `${Math.min(100, j.progress || 0)}%`,
                    height: "100%",
                    background: "var(--green)",
                    borderRadius: 2,
                  }}
                />
              </div>
            )}
            {(j.status === "pending" || j.status === "running") && (
              <button
                type="button"
                className="btn-secondary"
                style={{ marginTop: 6, fontSize: 11, padding: "2px 8px" }}
                onClick={() => void cancelJob(j.id).then(refresh)}
              >
                <XCircle size={12} /> 取消
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/** 供状态栏显示进行中数量 */
export function useActiveJobCount(pollMs = 4000): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    let alive = true;
    const tick = () =>
      listJobs({ limit: 30 })
        .then((r) => {
          if (!alive) return;
          setN((r.jobs || []).filter((j) => j.status === "pending" || j.status === "running").length);
        })
        .catch(() => alive && setN(0));
    tick();
    const t = window.setInterval(tick, pollMs);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [pollMs]);
  return n;
}
