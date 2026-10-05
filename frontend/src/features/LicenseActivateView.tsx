import { FormEvent, useState } from "react";
import { KeyRound, Loader2, LogOut, ShieldCheck } from "lucide-react";
import {
  AuthError,
  activateLicense,
  getMachineId,
  type AuthSession,
  type AuthUser,
} from "../api/client";

interface Props {
  user: AuthUser;
  onActivated: (session: AuthSession) => void;
  onLogout: () => void;
  /** 授权仍有效时也可打开（续费），显示关闭 */
  onClose?: () => void;
  allowSkip?: boolean;
}

export default function LicenseActivateView({
  user,
  onActivated,
  onLogout,
  onClose,
  allowSkip = false,
}: Props) {
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [okMsg, setOkMsg] = useState("");
  const machineId = getMachineId();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!code.trim()) {
      setError("请输入授权卡密");
      return;
    }
    setLoading(true);
    setError("");
    setOkMsg("");
    try {
      const session = await activateLicense(code.trim());
      setOkMsg(
        session.user.expire_at
          ? `激活成功，有效期至 ${session.user.expire_at.slice(0, 10)}`
          : "激活成功",
      );
      onActivated(session);
    } catch (e) {
      setError(e instanceof AuthError || e instanceof Error ? e.message : "激活失败");
    } finally {
      setLoading(false);
    }
  };

  const expiredHint =
    user.license_reason ||
    (user.expire_at ? `授权状态：${user.plan} · 到期 ${user.expire_at.slice(0, 10)}` : "尚未激活正式授权");

  return (
    <main className="auth-shell">
      <section className="auth-card" style={{ width: "min(420px, calc(100vw - 32px))" }}>
        <div className="auth-brand" aria-hidden="true">
          <img src="/logo.png" alt="" width={64} height={64} />
        </div>
        <h1>软件授权激活</h1>
        <p>西瓜短剧Agent · 卡密加密授权</p>

        <div
          style={{
            marginBottom: 16,
            padding: "10px 12px",
            borderRadius: 10,
            border: "1px solid var(--border2)",
            background: "var(--panel2)",
            fontSize: 12,
            color: "var(--text2)",
            lineHeight: 1.55,
          }}
        >
          <div>
            账号 <strong style={{ color: "var(--text)" }}>{user.username}</strong>
            {" · "}套餐 <strong style={{ color: "var(--text)" }}>{user.plan}</strong>
          </div>
          <div style={{ marginTop: 4, color: user.license_active ? "var(--green)" : "var(--amber, #e6a23c)" }}>
            {user.license_active ? (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                <ShieldCheck size={13} /> 授权有效
              </span>
            ) : (
              expiredHint
            )}
          </div>
          <div style={{ marginTop: 6, color: "var(--text3)", wordBreak: "break-all" }}>
            设备码：{machineId}
            <br />
            已绑定 {user.machines_bound}/{user.max_machines} 台
          </div>
        </div>

        <form onSubmit={(e) => void submit(e)} className="auth-form">
          <label>
            <span>
              <KeyRound size={14} /> 授权卡密
            </span>
            <input
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              placeholder="XG-XXXX-XXXX-XXXX-XXXX"
              autoFocus
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          {error && (
            <div className="form-error" role="alert">
              {error}
            </div>
          )}
          {okMsg && (
            <div style={{ color: "var(--green)", fontSize: 12 }} role="status">
              {okMsg}
            </div>
          )}
          <button className="btn-primary auth-submit" disabled={loading} type="submit">
            {loading ? (
              <>
                <Loader2 size={14} className="spin" /> 激活中…
              </>
            ) : (
              "激活 / 续费"
            )}
          </button>
        </form>

        <div style={{ display: "flex", gap: 8, marginTop: 12, justifyContent: "center" }}>
          {(allowSkip || user.license_active) && onClose && (
            <button type="button" className="btn-secondary" onClick={onClose}>
              {user.license_active ? "返回工作台" : "稍后激活"}
            </button>
          )}
          <button type="button" className="btn-secondary" onClick={onLogout}>
            <LogOut size={14} /> 退出登录
          </button>
        </div>

        <p style={{ marginTop: 16, fontSize: 11, color: "var(--text3)", textAlign: "center", lineHeight: 1.5 }}>
          新用户默认试用 {user.plan === "trial" ? "期内" : ""}可体验；正式使用请向作者购买卡密。
          <br />
          卡密与设备绑定，请勿泄露。
        </p>
      </section>
    </main>
  );
}
