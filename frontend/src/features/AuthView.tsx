import { useEffect, useState, type FormEvent } from "react";
import { KeyRound, Loader2, LockKeyhole, Server, UserRound, Wifi, WifiOff } from "lucide-react";
import {
  AuthError,
  authenticate,
  changePassword,
  type AuthSession,
} from "../api/client";

const AUTH_API = import.meta.env.VITE_AUTH_URL || "http://127.0.0.1:8100";

type AuthMode = "login" | "register" | "reset";

interface AuthViewProps {
  onAuthenticated: (session: AuthSession) => void;
  onBanned: (reason?: string) => void;
}

export default function AuthView({ onAuthenticated, onBanned }: AuthViewProps) {
  const [mode, setMode] = useState<AuthMode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newPassword2, setNewPassword2] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [authOnline, setAuthOnline] = useState<boolean | null>(null);

  const pingAuth = async () => {
    try {
      const controller = new AbortController();
      const t = window.setTimeout(() => controller.abort(), 2500);
      const r = await fetch(`${AUTH_API}/health`, { signal: controller.signal });
      window.clearTimeout(t);
      setAuthOnline(r.ok);
      return r.ok;
    } catch {
      setAuthOnline(false);
      return false;
    }
  };

  useEffect(() => {
    void pingAuth();
    const timer = window.setInterval(() => void pingAuth(), 5000);
    return () => window.clearInterval(timer);
  }, []);

  const ensureAuthOnline = async (): Promise<boolean> => {
    const online = await pingAuth();
    if (!online) {
      setError(
        "注册/登录服务未启动（端口 8100）。请先双击「E:\\xigua Agent  密码管理\\一键启动.bat」或运行软件启动脚本后再试。",
      );
      return false;
    }
    return true;
  };

  const submitLoginOrRegister = async (event: FormEvent) => {
    event.preventDefault();
    const normalizedUsername = username.trim();
    if (!normalizedUsername || !password) {
      setError("请输入用户名和密码");
      return;
    }
    setLoading(true);
    setError("");
    setSuccess("");
    try {
      if (!(await ensureAuthOnline())) return;
      const session = await authenticate(mode as "login" | "register", normalizedUsername, password);
      if (session.user.banned) {
        onBanned(session.user.banned_reason ?? undefined);
        return;
      }
      onAuthenticated(session);
    } catch (requestError) {
      if (requestError instanceof AuthError && requestError.banned) {
        onBanned(requestError.reason);
      } else {
        const msg = requestError instanceof Error ? requestError.message : "认证服务暂不可用";
        setError(
          msg.includes("认证服务") || msg.includes("不可用")
            ? `${msg}。请确认 8100 授权服务已打开（卡密管理平台一键启动）。`
            : msg,
        );
      }
    } finally {
      setLoading(false);
    }
  };

  const submitReset = async (event: FormEvent) => {
    event.preventDefault();
    const normalizedUsername = username.trim();
    if (!normalizedUsername) {
      setError("请输入用户名");
      return;
    }
    if (!oldPassword) {
      setError("请输入旧密码");
      return;
    }
    if (!newPassword || newPassword.length < 6) {
      setError("新密码至少 6 位");
      return;
    }
    if (newPassword !== newPassword2) {
      setError("两次输入的新密码不一致");
      return;
    }
    if (oldPassword === newPassword) {
      setError("新密码不能与旧密码相同");
      return;
    }
    setLoading(true);
    setError("");
    setSuccess("");
    try {
      if (!(await ensureAuthOnline())) return;
      const result = await changePassword(normalizedUsername, oldPassword, newPassword);
      setSuccess(result.message || "密码已修改，请使用新密码登录");
      setOldPassword("");
      setNewPassword("");
      setNewPassword2("");
      setPassword("");
      setMode("login");
    } catch (requestError) {
      if (requestError instanceof AuthError && requestError.banned) {
        onBanned(requestError.reason);
      } else {
        const msg = requestError instanceof Error ? requestError.message : "重置失败";
        setError(msg);
      }
    } finally {
      setLoading(false);
    }
  };

  const changeMode = (nextMode: AuthMode) => {
    setMode(nextMode);
    setError("");
    setSuccess("");
  };

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <div className="auth-brand" aria-hidden="true">
          <img src="/logo.png" alt="" width={64} height={64} />
        </div>
        <h1>西瓜短剧Agent</h1>
        <p>登录 / 注册 / 重置密码 · 与密码管理平台共用授权库</p>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            justifyContent: "center",
            marginBottom: 14,
            fontSize: 12,
            color: authOnline ? "var(--green)" : authOnline === false ? "var(--amber, #e6a23c)" : "var(--text3)",
          }}
        >
          {authOnline === null ? (
            <><Loader2 size={13} className="spin" /> 正在检测注册服务…</>
          ) : authOnline ? (
            <><Wifi size={13} /> 注册服务在线 (:8100)</>
          ) : (
            <><WifiOff size={13} /> 注册服务离线 — 请先启动 8100</>
          )}
        </div>

        {authOnline === false && (
          <div
            style={{
              marginBottom: 14,
              padding: "10px 12px",
              borderRadius: 10,
              border: "1px solid rgba(230,162,60,.4)",
              background: "rgba(230,162,60,.1)",
              fontSize: 12,
              color: "var(--text2)",
              lineHeight: 1.55,
            }}
          >
            <div style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
              <Server size={14} style={{ marginTop: 2, flexShrink: 0 }} />
              <div>
                登录注册没有消失，只是后台服务没开。请先：
                <br />
                1）双击 <code>E:\xigua Agent  密码管理\一键启动.bat</code>
                <br />
                2）或运行软件的 <code>scripts\启动.bat</code>（会自动拉起 8100）
                <br />
                3）回到本页点下方「重新检测」后再登录
              </div>
            </div>
            <button
              type="button"
              className="btn-secondary"
              style={{ marginTop: 10, width: "100%" }}
              onClick={() => void pingAuth()}
            >
              重新检测注册服务
            </button>
          </div>
        )}

        <div className="auth-tabs" role="tablist" aria-label="账号操作">
          <button className={mode === "login" ? "active" : ""} onClick={() => changeMode("login")} type="button">
            登录
          </button>
          <button className={mode === "register" ? "active" : ""} onClick={() => changeMode("register")} type="button">
            注册
          </button>
          <button className={mode === "reset" ? "active" : ""} onClick={() => changeMode("reset")} type="button">
            重置密码
          </button>
        </div>

        {mode === "reset" ? (
          <form onSubmit={(e) => void submitReset(e)} className="auth-form">
            <p style={{ margin: "0 0 10px", fontSize: 12, color: "var(--text2)", lineHeight: 1.55 }}>
              记得旧密码：在此直接改新密码（写入同一 auth.db）。
              <br />
              <strong>忘记旧密码</strong>：请联系管理员，在密码管理页
              <code style={{ margin: "0 4px" }}>http://127.0.0.1:8787</code>
              「重置登录密码」设新密码后再登录。
            </p>
            <label>
              <span>
                <UserRound size={14} /> 用户名
              </span>
              <input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                autoFocus
                maxLength={64}
              />
            </label>
            <label>
              <span>
                <LockKeyhole size={14} /> 旧密码
              </span>
              <input
                value={oldPassword}
                onChange={(event) => setOldPassword(event.target.value)}
                type="password"
                autoComplete="current-password"
              />
            </label>
            <label>
              <span>
                <KeyRound size={14} /> 新密码
              </span>
              <input
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
                type="password"
                autoComplete="new-password"
                minLength={6}
                maxLength={128}
              />
            </label>
            <label>
              <span>
                <KeyRound size={14} /> 确认新密码
              </span>
              <input
                value={newPassword2}
                onChange={(event) => setNewPassword2(event.target.value)}
                type="password"
                autoComplete="new-password"
                minLength={6}
                maxLength={128}
              />
            </label>
            {error && (
              <div className="form-error" role="alert">
                {error}
              </div>
            )}
            {success && (
              <div
                role="status"
                style={{
                  padding: "8px 10px",
                  borderRadius: 8,
                  background: "rgba(63,185,80,.12)",
                  border: "1px solid rgba(63,185,80,.35)",
                  color: "var(--green)",
                  fontSize: 13,
                }}
              >
                {success}
              </div>
            )}
            <button className="btn-primary auth-submit" disabled={loading} type="submit">
              {loading ? (
                <>
                  <Loader2 size={14} className="spin" /> 请稍候…
                </>
              ) : (
                "确认修改密码"
              )}
            </button>
          </form>
        ) : (
          <form onSubmit={(e) => void submitLoginOrRegister(e)} className="auth-form">
            <label>
              <span>
                <UserRound size={14} /> 用户名
              </span>
              <input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                autoFocus
                maxLength={64}
              />
            </label>
            <label>
              <span>
                <LockKeyhole size={14} /> 密码
              </span>
              <input
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                type="password"
                autoComplete={mode === "login" ? "current-password" : "new-password"}
              />
            </label>
            {error && (
              <div className="form-error" role="alert">
                {error}
                {mode === "login" && (
                  <div style={{ marginTop: 6, fontSize: 12, opacity: 0.9 }}>
                    密码不对？可点上方「重置密码」；若已忘记旧密码，请管理员在 8787 管理页重置。
                  </div>
                )}
              </div>
            )}
            {success && (
              <div
                role="status"
                style={{
                  padding: "8px 10px",
                  borderRadius: 8,
                  background: "rgba(63,185,80,.12)",
                  border: "1px solid rgba(63,185,80,.35)",
                  color: "var(--green)",
                  fontSize: 13,
                }}
              >
                {success}
              </div>
            )}
            <button className="btn-primary auth-submit" disabled={loading} type="submit">
              {loading ? (
                <>
                  <Loader2 size={14} className="spin" /> 请稍候…
                </>
              ) : mode === "login" ? (
                "登录"
              ) : (
                "注册并登录"
              )}
            </button>
          </form>
        )}
      </section>
    </main>
  );
}
