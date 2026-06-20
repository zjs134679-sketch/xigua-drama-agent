import { useState, type FormEvent } from "react";
import { Loader2, LockKeyhole, UserRound } from "lucide-react";
import {
  AuthError,
  authenticate,
  type AuthSession,
} from "../api/client";

interface AuthViewProps {
  onAuthenticated: (session: AuthSession) => void;
  onBanned: (reason?: string) => void;
}

export default function AuthView({ onAuthenticated, onBanned }: AuthViewProps) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const normalizedUsername = username.trim();
    if (!normalizedUsername || !password) {
      setError("请输入用户名和密码");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const session = await authenticate(mode, normalizedUsername, password);
      if (session.user.banned) {
        onBanned(session.user.banned_reason ?? undefined);
        return;
      }
      onAuthenticated(session);
    } catch (requestError) {
      if (requestError instanceof AuthError && requestError.banned) {
        onBanned(requestError.reason);
      } else {
        setError(requestError instanceof Error ? requestError.message : "认证服务暂不可用");
      }
    } finally {
      setLoading(false);
    }
  };

  const changeMode = (nextMode: "login" | "register") => {
    setMode(nextMode);
    setError("");
  };

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <div className="auth-brand" aria-hidden="true"><img src="/logo.png" alt="" width={64} height={64} /></div>
        <h1>西瓜短剧Agent</h1>
        <p>登录后进入短剧创作工作台</p>

        <div className="auth-tabs" role="tablist" aria-label="账号操作">
          <button className={mode === "login" ? "active" : ""} onClick={() => changeMode("login")} type="button">
            登录
          </button>
          <button className={mode === "register" ? "active" : ""} onClick={() => changeMode("register")} type="button">
            注册
          </button>
        </div>

        <form onSubmit={submit} className="auth-form">
          <label>
            <span><UserRound size={14} /> 用户名</span>
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              autoFocus
              maxLength={64}
            />
          </label>
          <label>
            <span><LockKeyhole size={14} /> 密码</span>
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
            />
          </label>
          {error && <div className="form-error" role="alert">{error}</div>}
          <button className="btn-primary auth-submit" disabled={loading} type="submit">
            {loading ? <><Loader2 size={14} className="spin" /> 请稍候…</> : mode === "login" ? "登录" : "注册并登录"}
          </button>
        </form>
      </section>
    </main>
  );
}
