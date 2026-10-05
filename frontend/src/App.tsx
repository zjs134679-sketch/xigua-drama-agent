import { useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  ChevronDown,
  Cpu,
  Download,
  FileText,
  Film,
  Folder,
  Image as ImageIcon,
  KeyRound,
  Layers,
  LayoutGrid,
  GitBranch,
  List,
  ListTodo,
  LogOut,
  Mountain,
  Play,
  Palette,
  Package,
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
  licenseHeartbeat,
  logout,
  loadAuthServerConfig,
  restoreAuthSession,
  syncCompliance,
  type AuthSession,
  type AuthUser,
  type ComplianceResult,
  type VersionInfo,
} from "./api/client";
import BanScreen from "./components/BanScreen";
import PipelineBar, { type PipelineViewId } from "./components/PipelineBar";
import ScriptView from "./components/ScriptView";
import SetupWizard, { shouldShowSetupWizard } from "./components/SetupWizard";
import TaskDrawer, { useActiveJobCount } from "./components/TaskDrawer";
import ArtStylesView from "./features/ArtStylesView";
import AuthView from "./features/AuthView";
import CharacterAssetsView from "./features/CharacterAssetsView";
import ComputeNodesView from "./features/ComputeNodesView";
import EventsView from "./features/EventsView";
import LicenseActivateView from "./features/LicenseActivateView";
import EasyStudioView from "./features/EasyStudioView";
import ProjectView from "./features/ProjectView";
import PropAssetsView from "./features/PropAssetsView";
import SceneAssetsView from "./features/SceneAssetsView";
import SettingsView from "./features/SettingsView";
import StoryboardView from "./features/StoryboardView";
import StoryboardReviewView from "./features/StoryboardReviewView";
import TimelineView from "./features/TimelineView";
import type { EpisodeSummary, Project } from "./api/client";

type ViewId =
  | "easy"
  | "project"
  | "storyboard"
  | "review"
  | "script"
  | "events"
  | "characters"
  | "scenes"
  | "props"
  | "art-styles"
  | "timeline"
  | "compute"
  | "settings";
type CurrentSelection = { drama: Project; episode: EpisodeSummary } | null;

const MODE_KEY = "xigua.ui.mode"; // easy | pro

function WatermelonLogo({ size = 22 }: { size?: number }) {
  return <img src="/logo.png" alt="西瓜短剧Agent" width={size} height={size} style={{ display: "block", objectFit: "contain" }} />;
}

/** 小白默认：只露出核心入口 */
const RAIL_EASY: { icon: typeof Folder; label: string; view?: ViewId }[] = [
  { icon: Wand2, label: "一键出片", view: "easy" },
  { icon: Folder, label: "我的项目", view: "project" },
  { icon: Film, label: "成片", view: "timeline" },
  { icon: Server, label: "算力", view: "compute" },
];

const RAIL_PRO: { icon: typeof Folder; label: string; view?: ViewId }[] = [
  { icon: Wand2, label: "一键出片", view: "easy" },
  { icon: Folder, label: "项目", view: "project" },
  { icon: FileText, label: "剧本", view: "script" },
  { icon: GitBranch, label: "事件图谱", view: "events" },
  { icon: Palette, label: "画风库", view: "art-styles" },
  { icon: Users, label: "角色资产", view: "characters" },
  { icon: Mountain, label: "场景造景", view: "scenes" },
  { icon: Package, label: "道具", view: "props" },
  // 镜头脚本（非静帧出图）→ Agent 审核 → 多图参考生视频
  { icon: LayoutGrid, label: "镜头脚本", view: "storyboard" },
  { icon: ClipboardCheck, label: "Agent审核", view: "review" },
  { icon: Film, label: "成片", view: "timeline" },
  { icon: Server, label: "算力", view: "compute" },
];

export default function App() {
  const [uiMode, setUiMode] = useState<"easy" | "pro">(() => {
    try {
      return localStorage.getItem(MODE_KEY) === "pro" ? "pro" : "easy";
    } catch {
      return "easy";
    }
  });
  const [view, setView] = useState<ViewId>(() => (localStorage.getItem(MODE_KEY) === "pro" ? "project" : "easy"));
  const [current, setCurrent] = useState<CurrentSelection>(null);
  const [authReady, setAuthReady] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [updateInfo, setUpdateInfo] = useState<VersionInfo | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [banned, setBanned] = useState(false);
  const [banReason, setBanReason] = useState<string | undefined>();
  const [dictionaryVersion, setDictionaryVersion] = useState<string | null>(null);
  const [taskOpen, setTaskOpen] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(() => shouldShowSetupWizard());
  const [licenseModal, setLicenseModal] = useState(false);
  const activeJobs = useActiveJobCount();
  const RAIL = uiMode === "easy" ? RAIL_EASY : RAIL_PRO;

  const toggleUiMode = () => {
    const next = uiMode === "easy" ? "pro" : "easy";
    setUiMode(next);
    try {
      localStorage.setItem(MODE_KEY, next);
    } catch {
      /* ignore */
    }
    if (next === "easy") setView("easy");
  };

  useEffect(() => {
    let alive = true;
    // 先从本机后端读取 XIGUA_AUTH_SERVER_URL（客户包=公网；开发=127.0.0.1:8100）
    loadAuthServerConfig()
      .then(() => restoreAuthSession())
      .then((session) => {
        if (!alive || !session) return;
        if (session.user.banned) {
          setBanned(true);
          setBanReason(session.user.banned_reason ?? undefined);
          return;
        }
        setUser(session.user);
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

  // 授权心跳：每 6 小时校验一次 plan/到期/机器绑定
  useEffect(() => {
    if (!user || user.banned) return;
    let alive = true;
    const beat = () => {
      licenseHeartbeat()
        .then((next) => {
          if (!alive) return;
          setUser(next);
          if (next.banned) {
            setBanned(true);
            setBanReason(next.banned_reason ?? undefined);
          }
        })
        .catch((error) => {
          if (!alive) return;
          if (error instanceof AuthError && error.banned) {
            setBanned(true);
            setBanReason(error.reason);
          } else if (error instanceof AuthError && error.status === 403) {
            // 授权失效：刷新本地 user 状态（下次渲染进激活页）
            setUser((current) =>
              current
                ? {
                    ...current,
                    license_active: false,
                    license_reason: error.message || current.license_reason,
                  }
                : current,
            );
          }
        });
    };
    // 立刻心跳一次，之后每 5 分钟（管理端「近12h在线」依赖 last_seen；原先 6h 且不立即发 → 常显示离线）
    beat();
    const timer = window.setInterval(beat, 5 * 60 * 1000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [user?.username, user?.banned]);

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
    setLicenseModal(false);
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
    setLicenseModal(false);
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

  // 强制授权：未激活 / 过期时进入卡密页（license_required=false 的开发环境可跳过）
  const mustActivate = user.license_required !== false && !user.license_active;
  if (mustActivate || licenseModal) {
    return (
      <LicenseActivateView
        user={user}
        onActivated={handleAuthenticated}
        onLogout={handleLogout}
        onClose={mustActivate ? undefined : () => setLicenseModal(false)}
        allowSkip={!mustActivate}
      />
    );
  }

  const planLabel = user.plan || "free";
  const expireLabel = user.expire_at ? user.expire_at.slice(0, 10) : "长期";

  return (
    <div style={{ position: "relative", height: "100vh", display: "flex", flexDirection: "column", background: "var(--bg)" }}>
      {wizardOpen && <SetupWizard onClose={() => setWizardOpen(false)} />}

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
          {current ? `${current.drama.title} · 第${current.episode.episode_number}集 ${current.episode.title}` : "新手友好 · 国内版"}
        </span>
        <button
          type="button"
          className="pill"
          title={uiMode === "easy" ? "切换到专业模式（全部功能）" : "切换到新手模式（精简菜单）"}
          style={{
            border: "1px solid var(--border2)",
            color: uiMode === "easy" ? "var(--green-t)" : "var(--blue-t)",
            background: uiMode === "easy" ? "rgba(43,178,76,0.12)" : "rgba(91,141,239,0.12)",
            cursor: "pointer",
          }}
          onClick={toggleUiMode}
        >
          {uiMode === "easy" ? "新手模式" : "专业模式"}
        </button>
        <div style={{ flex: 1 }} />
        <div className="pill" style={{ border: "1px solid var(--border2)", color: "var(--text2)" }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: online ? "var(--green)" : "var(--text3)" }} />
          ComfyUI · {online == null ? "检测中" : online ? "在线" : "离线"}
          <ChevronDown size={13} />
        </div>
        <button
          type="button"
          className="pill"
          title="点击管理授权 / 续费"
          style={{
            border: "1px solid var(--border2)",
            color: user.license_active ? "var(--green-t)" : "var(--amber, #e6a23c)",
            background: user.license_active ? "rgba(43,178,76,0.12)" : "rgba(230,162,60,0.12)",
            cursor: "pointer",
          }}
          onClick={() => setLicenseModal(true)}
        >
          <KeyRound size={13} /> {planLabel} · {expireLabel}
        </button>
        <div className="pill" style={{ background: "rgba(43,178,76,0.16)", color: "var(--green-t)" }}>
          <ShieldCheck size={14} /> 合规
        </div>
        <button
          type="button"
          className="pill"
          style={{ border: "1px solid var(--border2)", color: "var(--text2)", cursor: "pointer" }}
          onClick={() => setTaskOpen((v) => !v)}
          title="生产任务队列"
        >
          <ListTodo size={14} /> 任务 {activeJobs > 0 ? activeJobs : ""}
        </button>
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

        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", minHeight: 0 }}>
          {uiMode === "pro" || view !== "easy" ? (
            <PipelineBar
              drama={current?.drama ?? null}
              episode={current?.episode ?? null}
              currentView={view}
              onGoto={(v: PipelineViewId) => setView(v)}
            />
          ) : null}
          <div style={{ flex: 1, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column" }}>
        {view === "easy" ? (
          <EasyStudioView
            username={user.username}
            current={current}
            onOpenEpisode={(drama, episode) => {
              setCurrent({ drama, episode });
            }}
            onGotoPro={(v) => {
              setUiMode("pro");
              try {
                localStorage.setItem(MODE_KEY, "pro");
              } catch {
                /* ignore */
              }
              setView(v as ViewId);
            }}
          />
        ) : view === "project" ? (
          <ProjectView
            current={current}
            username={user.username}
            onOpenEpisode={(drama, episode) => {
              setCurrent({ drama, episode });
              setView(uiMode === "easy" ? "easy" : "script");
            }}
          />
        ) : view === "script" ? (
          <ScriptView
            username={user.username}
            current={current}
            onBanned={() => setBanned(true)}
            onGoto={(v) => setView(v as ViewId)}
          />
        ) : view === "events" ? (
          <EventsView
            dramaId={current?.drama.id ?? null}
            username={user.username}
            onGotoScript={() => setView("script")}
            onGotoStoryboard={() => setView("storyboard")}
          />
        ) : view === "characters" ? (
          <CharacterAssetsView currentDramaId={current?.drama.id ?? null} currentEpisodeId={current?.episode.id ?? null} username={user.username} />
        ) : view === "scenes" ? (
          <SceneAssetsView currentDramaId={current?.drama.id ?? null} currentEpisodeId={current?.episode.id ?? null} username={user.username} />
        ) : view === "props" ? (
          <PropAssetsView currentDramaId={current?.drama.id ?? null} currentEpisodeId={current?.episode.id ?? null} username={user.username} />
        ) : view === "art-styles" ? (
          <ArtStylesView currentDramaId={current?.drama.id ?? null} />
        ) : view === "storyboard" ? (
          <StoryboardView
            current={current}
            username={user.username}
            onBanned={() => setBanned(true)}
            onGoto={(v) => setView(v as ViewId)}
          />
        ) : view === "review" ? (
          <StoryboardReviewView current={current} username={user.username} onOpenStoryboard={() => setView("storyboard")} />
        ) : view === "timeline" ? (
          <TimelineView currentDramaId={current?.drama.id ?? null} currentEpisodeId={current?.episode.id ?? null} username={user.username} />
        ) : view === "compute" ? (
          <ComputeNodesView />
        ) : view === "settings" ? (
          <SettingsView dramaId={current?.drama.id ?? null} episodeId={current?.episode.id ?? null} />
        ) : (
          <EasyStudioView
            username={user.username}
            current={current}
            onOpenEpisode={(drama, episode) => {
              setCurrent({ drama, episode });
            }}
            onGotoPro={(v) => {
              setUiMode("pro");
              try {
                localStorage.setItem(MODE_KEY, "pro");
              } catch {
                /* ignore */
              }
              setView(v as ViewId);
            }}
          />
        )}
          </div>
        </div>
      </div>

      <TaskDrawer open={taskOpen} onClose={() => setTaskOpen(false)} dramaId={current?.drama.id ?? null} />

      {/* 底部状态条 */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "6px 12px", borderTop: "1px solid var(--border)", background: "var(--panel)", fontSize: 11, color: "var(--text3)" }}>
        <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}><Cpu size={13} /> GPU {online ? "在线" : "—"}</span>
        <button
          type="button"
          style={{ display: "inline-flex", gap: 4, alignItems: "center", background: "none", border: 0, color: "inherit", cursor: "pointer", padding: 0, font: "inherit" }}
          onClick={() => setTaskOpen(true)}
        >
          <Layers size={13} /> 队列 {activeJobs}
        </button>
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
