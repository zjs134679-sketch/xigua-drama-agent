import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Cpu,
  Download,
  FileText,
  Film,
  Folder,
  Image as ImageIcon,
  Layers,
  LayoutGrid,
  List,
  LogOut,
  Play,
  Palette,
  Scissors,
  Server,
  Settings,
  ShieldCheck,
  SkipBack,
  Users,
  Wand2,
  X,
} from "lucide-react";
import {
  AuthError,
  checkCompliance,
  getComplianceStatus,
  getComputeHealth,
  getLatestVersion,
  isNewerVersion,
  logout,
  restoreAuthSession,
  syncCompliance,
  type AuthSession,
  type AuthUser,
  type ComplianceResult,
  type VersionInfo,
} from "./api/client";
import BanScreen from "./components/BanScreen";
import ScriptView from "./components/ScriptView";
import ArtStylesView from "./features/ArtStylesView";
import AuthView from "./features/AuthView";
import CharacterAssetsView from "./features/CharacterAssetsView";
import ComputeNodesView from "./features/ComputeNodesView";
import ProjectView from "./features/ProjectView";
import SettingsView from "./features/SettingsView";
import SponsorDialog from "./features/SponsorDialog";
import StoryboardView from "./features/StoryboardView";
import TimelineView from "./features/TimelineView";
import type { EpisodeSummary, Project } from "./api/client";

type ViewId = "project" | "storyboard" | "script" | "characters" | "art-styles" | "timeline" | "compute" | "settings";
type CurrentSelection = { drama: Project; episode: EpisodeSummary } | null;

function WatermelonLogo({ size = 22 }: { size?: number }) {
  return <img src="/logo.png" alt="西瓜短剧Agent" width={size} height={size} style={{ display: "block", objectFit: "contain" }} />;
}

const RAIL: { icon: typeof Folder; label: string; view?: ViewId }[] = [
  { icon: Folder, label: "项目", view: "project" },
  { icon: FileText, label: "剧本", view: "script" },
  { icon: Users, label: "角色资产", view: "characters" },
  { icon: Palette, label: "画风库", view: "art-styles" },
  { icon: LayoutGrid, label: "分镜", view: "storyboard" },
  { icon: Film, label: "成片", view: "timeline" },
  { icon: Server, label: "算力", view: "compute" },
];

export default function App() {
  const [view, setView] = useState<ViewId>("project");
  const [current, setCurrent] = useState<CurrentSelection>(null);
  const [authReady, setAuthReady] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [sponsorOpen, setSponsorOpen] = useState(false);
  const sponsorShown = useRef(false);
  const [updateInfo, setUpdateInfo] = useState<VersionInfo | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [banned, setBanned] = useState(false);
  const [banReason, setBanReason] = useState<string | undefined>();
  const [dictionaryVersion, setDictionaryVersion] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    restoreAuthSession()
      .then((session) => {
        if (!alive || !session) return;
        if (session.user.banned) {
          setBanned(true);
          setBanReason(session.user.banned_reason ?? undefined);
          return;
        }
        setUser(session.user);
        if (!sponsorShown.current) {
          sponsorShown.current = true;
          setSponsorOpen(true);
        }
      })
      .catch((error) => {
        if (!alive) return;
        if (error instanceof AuthError && error.banned) {
          setBanned(true);
          setBanReason(error.reason);
        } else {
          logout();
        }
      })
      .finally(() => alive && setAuthReady(true));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    getLatestVersion()
      .then((info) => {
        if (alive && isNewerVersion(info.latest)) setUpdateInfo(info);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    const ping = () => getComputeHealth().then((h) => alive && setOnline(h.online)).catch(() => alive && setOnline(false));
    ping();
    const t = setInterval(ping, 8000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  useEffect(() => {
    if (!user) return;
    syncCompliance()
      .then((status) => setDictionaryVersion(status.version))
      .catch(() => undefined);
    getComplianceStatus(user.username)
      .then((status) => {
        setBanned(status.banned);
        setBanReason(status.banned_reason ?? undefined);
      })
      .catch(() => undefined);
  }, [user]);

  const handleAuthenticated = (session: AuthSession) => {
    if (session.user.banned) {
      logout();
      setBanned(true);
      setBanReason(session.user.banned_reason ?? undefined);
      return;
    }
    setUser(session.user);
    setBanned(false);
    setBanReason(undefined);
    if (!sponsorShown.current) {
      sponsorShown.current = true;
      setSponsorOpen(true);
    }
  };

  const handleBanned = (reason?: string) => {
    logout();
    setUser(null);
    setBanned(true);
    setBanReason(reason);
  };

  const handleLogout = () => {
    logout();
    setUser(null);
    setBanned(false);
    setBanReason(undefined);
    setSponsorOpen(false);
  };

  if (!authReady) {
    return <div className="auth-loading"><WatermelonLogo size={38} /><span>正在连接账号服务…</span></div>;
  }

  if (banned) {
    return <div style={{ position: "relative", height: "100vh", background: "var(--bg)" }}><BanScreen reason={banReason} onLogout={handleLogout} /></div>;
  }

  if (!user) {
    return <AuthView onAuthenticated={handleAuthenticated} onBanned={handleBanned} />;
  }

  return (
    <div style={{ position: "relative", height: "100vh", display: "flex", flexDirection: "column", background: "var(--bg)" }}>
      {sponsorOpen && <SponsorDialog onClose={() => setSponsorOpen(false)} />}

      {updateInfo && (
        <div className="update-banner">
          <Download size={14} />
          <span>发现新版本 {updateInfo.latest}</span>
          {updateInfo.notes && <span className="update-notes">{updateInfo.notes}</span>}
          {updateInfo.url && <a href={updateInfo.url} target="_blank" rel="noreferrer">下载更新</a>}
          <button type="button" aria-label="关闭升级提醒" onClick={() => setUpdateInfo(null)}><X size={14} /></button>
        </div>
      )}

      {/* 顶栏 */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", borderBottom: "1px solid var(--border)", background: "var(--panel)" }}>
        <WatermelonLogo />
        <span style={{ fontSize: 13, fontWeight: 500 }}>西瓜短剧Agent</span>
        <span style={{ fontSize: 11, color: "var(--text3)" }}>
          {current ? `${current.drama.title} · 第${current.episode.episode_number}集 ${current.episode.title}` : "国内版"}
        </span>
        <div style={{ flex: 1 }} />
        <div className="pill" style={{ border: "1px solid var(--border2)", color: "var(--text2)" }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: online ? "var(--green)" : "var(--text3)" }} />
          本地 ComfyUI · {online == null ? "检测中" : online ? "在线" : "离线"}
          <ChevronDown size={13} />
        </div>
        <div className="pill" style={{ background: "rgba(43,178,76,0.16)", color: "var(--green-t)" }}>
          <ShieldCheck size={14} /> 合规
        </div>
        <div className="user-menu">
          <div className="user-avatar">{user.username.slice(0, 1).toUpperCase()}</div>
          <span title={user.username}>{user.username}</span>
          <button type="button" onClick={handleLogout}><LogOut size={13} /> 退出</button>
        </div>
      </div>

      {/* 主体 */}
      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        {/* 活动栏 */}
        <div style={{ width: 48, flex: "none", borderRight: "1px solid var(--border)", background: "var(--panel2)", display: "flex", flexDirection: "column", alignItems: "center", gap: 3, padding: "8px 0" }}>
          {RAIL.map((r) => (
            <button
              key={r.label}
              className={`rail-btn${r.view && view === r.view ? " active" : ""}`}
              title={r.label}
              onClick={() => r.view && setView(r.view)}
            >
              <r.icon size={19} />
            </button>
          ))}
          <div style={{ flex: 1 }} />
          <button className={`rail-btn${view === "settings" ? " active" : ""}`} title="设置" onClick={() => setView("settings")}><Settings size={19} /></button>
        </div>

        {view === "project" ? (
          <ProjectView
            current={current}
            username={user.username}
            onOpenEpisode={(drama, episode) => {
              setCurrent({ drama, episode });
              setView("script");
            }}
          />
        ) : view === "script" ? <ScriptView username={user.username} current={current} onBanned={() => setBanned(true)} /> : view === "characters" ? <CharacterAssetsView currentDramaId={current?.drama.id ?? null} /> : view === "art-styles" ? <ArtStylesView /> : view === "timeline" ? <TimelineView currentDramaId={current?.drama.id ?? null} currentEpisodeId={current?.episode.id ?? null} /> : view === "compute" ? <ComputeNodesView /> : view === "settings" ? <SettingsView /> : (
          <StoryboardView current={current} username={user.username} onBanned={() => setBanned(true)} />
        )}
      </div>

      {/* 底部状态条 */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "6px 12px", borderTop: "1px solid var(--border)", background: "var(--panel)", fontSize: 11, color: "var(--text3)" }}>
        <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}><Cpu size={13} /> GPU {online ? "在线" : "—"}</span>
        <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}><Layers size={13} /> 队列 0</span>
        <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
          <ShieldCheck size={13} /> {dictionaryVersion ? `词库 v${dictionaryVersion}` : "本地词库"}
        </span>
        <div style={{ flex: 1 }} />
        <span style={{ color: "var(--amber)", display: "inline-flex", gap: 4, alignItems: "center" }}>
          <AlertTriangle size={13} /> 内容须遵守中国法律法规 · 三次红线违规将封号
        </span>
      </div>
    </div>
  );
}
