import { useEffect, useState } from "react";
import { CheckCircle2, AlertTriangle, Loader2, Rocket, X } from "lucide-react";
import { getSetupWizard } from "../api/client";

const DISMISS_KEY = "xigua.setup.wizard.dismissed";

export default function SetupWizard({ onClose }: { onClose?: () => void }) {
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<Awaited<ReturnType<typeof getSetupWizard>> | null>(null);

  useEffect(() => {
    getSetupWizard()
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  const dismiss = (permanent: boolean) => {
    if (permanent) {
      try {
        localStorage.setItem(DISMISS_KEY, "1");
      } catch {
        /* ignore */
      }
    }
    onClose?.();
  };

  if (loading) {
    return (
      <div className="setup-wizard-mask">
        <div className="setup-wizard-card">
          <Loader2 className="spin" size={22} /> 检测运行环境…
        </div>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="setup-wizard-mask" role="dialog" aria-label="首次运行向导">
      <div className="setup-wizard-card">
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
          <Rocket size={18} />
          <strong>运行环境向导</strong>
          <span style={{ fontSize: 11, color: "var(--text3)" }}>v{data.version}</span>
          <div style={{ flex: 1 }} />
          <button type="button" className="rail-btn" onClick={() => dismiss(false)} aria-label="关闭">
            <X size={14} />
          </button>
        </div>
        <p style={{ fontSize: 12, color: "var(--text2)", marginBottom: 12 }}>
          {data.production_ready
            ? "环境 OK。文案走 API，画面走 ComfyUI。点左侧「一键出片」开始。"
            : "第一次使用：① 设置里填语言模型 API（只写剧本）② 启动本机 ComfyUI 出图出片。"}
        </p>
        <ul style={{ listStyle: "none", padding: 0, margin: 0, maxHeight: 320, overflow: "auto" }}>
          {data.steps.map((s) => (
            <li
              key={s.id}
              style={{
                border: "1px solid var(--border2)",
                borderRadius: 8,
                padding: "10px 12px",
                marginBottom: 8,
                background: "var(--panel2)",
              }}
            >
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                {s.ok ? <CheckCircle2 size={15} color="var(--green)" /> : <AlertTriangle size={15} color="var(--amber)" />}
                <strong style={{ fontSize: 13 }}>{s.title}</strong>
              </div>
              <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 4 }}>{s.detail}</div>
              {!s.ok && s.fix && (
                <div style={{ fontSize: 11, color: "var(--amber)", marginTop: 4 }}>→ {s.fix}</div>
              )}
              {s.gpu_hint && (
                <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 4 }}>{s.gpu_hint}</div>
              )}
            </li>
          ))}
        </ul>
        <div style={{ fontSize: 11, color: "var(--text3)", margin: "10px 0" }}>
          建议：{(data.next_actions || []).join(" · ") || "设置填文案 API → 启动 ComfyUI → 一键出片"}
        </div>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button type="button" className="btn-secondary" onClick={() => dismiss(true)}>
            不再提示
          </button>
          <button type="button" className="btn-primary" onClick={() => dismiss(false)}>
            知道了，去出片
          </button>
        </div>
      </div>
      <style>{`
        .setup-wizard-mask {
          position: fixed; inset: 0; z-index: 100;
          background: rgba(0,0,0,0.55);
          display: flex; align-items: center; justify-content: center;
        }
        .setup-wizard-card {
          width: min(480px, 92vw);
          background: var(--panel);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 18px 20px;
          box-shadow: 0 20px 60px rgba(0,0,0,0.5);
        }
      `}</style>
    </div>
  );
}

export function shouldShowSetupWizard(): boolean {
  try {
    return localStorage.getItem(DISMISS_KEY) !== "1";
  } catch {
    return true;
  }
}
