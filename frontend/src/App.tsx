import { useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Cpu,
  FileText,
  Film,
  Folder,
  Image as ImageIcon,
  Layers,
  LayoutGrid,
  List,
  Play,
  Palette,
  Scissors,
  Server,
  Settings,
  ShieldCheck,
  SkipBack,
  Users,
  Wand2,
} from "lucide-react";
import {
  checkCompliance,
  getComplianceStatus,
  getComputeHealth,
  syncCompliance,
  type ComplianceResult,
} from "./api/client";
import BanScreen from "./components/BanScreen";
import ScriptView from "./components/ScriptView";
import ArtStylesView from "./features/ArtStylesView";
import CharacterAssetsView from "./features/CharacterAssetsView";
import TimelineView from "./features/TimelineView";

type ViewId = "storyboard" | "script" | "characters" | "art-styles" | "timeline";

function WatermelonLogo({ size = 22 }: { size?: number }) {
  return (
    <svg viewBox="0 0 26 15" width={size} height={(size * 15) / 26} fill="none" strokeLinecap="round">
      <path d="M2 2.5 A11 11 0 0 0 24 2.5" stroke="#23B24C" strokeWidth="1.6" />
      {[9.3, 7.7, 6.1, 4.5, 2.9, 1.3].map((r) => (
        <path key={r} d={`M${13 - r} 2.5 A${r} ${r} 0 0 0 ${13 + r} 2.5`} stroke="#F23A38" strokeWidth="1.2" />
      ))}
    </svg>
  );
}

const RAIL: { icon: typeof Folder; label: string; view?: ViewId }[] = [
  { icon: Folder, label: "项目" },
  { icon: FileText, label: "剧本", view: "script" },
  { icon: Users, label: "角色资产", view: "characters" },
  { icon: Palette, label: "画风库", view: "art-styles" },
  { icon: LayoutGrid, label: "分镜", view: "storyboard" },
  { icon: Film, label: "成片", view: "timeline" },
  { icon: Server, label: "算力" },
];

const SHOTS = [
  { no: "01", text: "雨夜 · 邮局门口 · 撑伞回望", dot: "var(--green)" },
  { no: "02", text: "柜台前 · 女主递出旧信封", dot: "var(--blue)", active: true },
  { no: "03", text: "回忆闪回 · 战火街道", dot: "var(--amber)", warn: true },
];

const TRACKS = [
  { label: "视频", color: "#5B8DEF", segs: [34, 26, 22] },
  { label: "配音", color: "#3FB58E", segs: [48, 30] },
  { label: "字幕", color: "#D99A2B", segs: [20, 20, 20] },
];

export default function App() {
  const [view, setView] = useState<ViewId>("script");
  const [online, setOnline] = useState<boolean | null>(null);
  const [text, setText] = useState("柜台前，女主低头递出一封泛黄的旧信封，暖色灯光，电影质感");
  const [result, setResult] = useState<ComplianceResult | null>(null);
  const [banned, setBanned] = useState(false);
  const [banReason, setBanReason] = useState<string | undefined>();
  const [dictionaryVersion, setDictionaryVersion] = useState<string | null>(null);

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
    syncCompliance()
      .then((status) => setDictionaryVersion(status.version))
      .catch(() => undefined);
    getComplianceStatus()
      .then((status) => {
        setBanned(status.banned);
        setBanReason(status.banned_reason ?? undefined);
      })
      .catch(() => undefined);
  }, []);

  const runCheck = async () => {
    try {
      setResult(await checkCompliance(text));
    } catch {
      setResult(null);
    }
  };

  return (
    <div style={{ position: "relative", height: "100vh", display: "flex", flexDirection: "column", background: "var(--bg)" }}>
      {banned && <BanScreen reason={banReason} />}

      {/* 顶栏 */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", borderBottom: "1px solid var(--border)", background: "var(--panel)" }}>
        <WatermelonLogo />
        <span style={{ fontSize: 13, fontWeight: 500 }}>西瓜短剧Agent</span>
        <span style={{ fontSize: 11, color: "var(--text3)" }}>国内版 · 第3集 时光邮局</span>
        <div style={{ flex: 1 }} />
        <div className="pill" style={{ border: "1px solid var(--border2)", color: "var(--text2)" }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: online ? "var(--green)" : "var(--text3)" }} />
          本地 ComfyUI · {online == null ? "检测中" : online ? "在线" : "离线"}
          <ChevronDown size={13} />
        </div>
        <div className="pill" style={{ background: "rgba(43,178,76,0.16)", color: "var(--green-t)" }}>
          <ShieldCheck size={14} /> 合规
        </div>
        <div style={{ width: 22, height: 22, borderRadius: "50%", background: "rgba(80,140,255,0.18)", color: "var(--blue-t)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, fontWeight: 500 }}>瓜</div>
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
          <button className="rail-btn" title="设置"><Settings size={19} /></button>
        </div>

        {view === "script" ? <ScriptView onBanned={() => setBanned(true)} /> : view === "characters" ? <CharacterAssetsView /> : view === "art-styles" ? <ArtStylesView /> : view === "timeline" ? <TimelineView /> : (
          <>
            {/* 中部 */}
            <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
                <span style={{ fontSize: 12, fontWeight: 500 }}>分镜台</span>
                <span style={{ fontSize: 11, color: "var(--text3)" }}>12 个镜头</span>
                <div style={{ flex: 1 }} />
                <span className="pill" style={{ border: "1px solid var(--green)", color: "var(--green-t)" }}><Wand2 size={13} /> 一键生成</span>
                <LayoutGrid size={15} color="var(--blue-t)" />
                <List size={15} color="var(--text3)" />
              </div>

              <div style={{ padding: 12, display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10 }}>
                {SHOTS.map((s) => (
                  <div key={s.no} className="card" style={{ border: s.active ? "2px solid var(--blue)" : undefined }}>
                    <div style={{ position: "relative", height: 76, background: "var(--surface)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                      <ImageIcon size={22} color="var(--text3)" />
                      <span style={{ position: "absolute", left: 6, top: 6, fontSize: 11, padding: "1px 6px", borderRadius: 4, background: s.active ? "rgba(80,140,255,0.2)" : "var(--bg)", color: s.active ? "var(--blue-t)" : "var(--text2)" }}>镜头 {s.no}</span>
                      <span style={{ position: "absolute", right: 6, top: 7, width: 7, height: 7, borderRadius: "50%", background: s.dot }} />
                    </div>
                    <p style={{ margin: 0, padding: "7px 8px", fontSize: 11, color: "var(--text2)", lineHeight: 1.4 }}>{s.text}</p>
                    {s.warn && (
                      <div style={{ padding: "0 8px 7px" }}>
                        <span className="pill" style={{ background: "rgba(224,160,27,0.16)", color: "var(--amber)" }}><AlertTriangle size={12} /> 黄线提示</span>
                      </div>
                    )}
                  </div>
                ))}
              </div>

              <div style={{ marginTop: "auto", borderTop: "1px solid var(--border)", padding: "10px 12px", background: "var(--panel)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                  <Film size={14} color="var(--text2)" />
                  <span style={{ fontSize: 11, color: "var(--text2)" }}>成片时间线</span>
                  <span style={{ fontSize: 11, color: "var(--text3)" }}>00:42 / 02:10</span>
                  <div style={{ flex: 1 }} />
                  <SkipBack size={14} color="var(--text3)" />
                  <Play size={15} color="var(--text)" />
                  <Scissors size={14} color="var(--text3)" />
                </div>
                <div style={{ position: "relative" }}>
                  {TRACKS.map((t) => (
                    <div key={t.label} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                      <span style={{ width: 34, fontSize: 11, color: "var(--text3)", flex: "none" }}>{t.label}</span>
                      <div style={{ flex: 1, height: 16, borderRadius: 4, background: "var(--surface)", display: "flex", gap: 3, padding: 2 }}>
                        {t.segs.map((w, i) => (
                          <div key={i} style={{ width: `${w}%`, background: t.color, borderRadius: 3 }} />
                        ))}
                      </div>
                    </div>
                  ))}
                  <div style={{ position: "absolute", top: 0, bottom: 0, left: "46%", width: 2, background: "var(--red)" }} />
                </div>
              </div>
            </div>

            {/* Inspector：实时合规检测 */}
            <div style={{ width: 230, flex: "none", borderLeft: "1px solid var(--border)", padding: 12, background: "var(--bg)", overflowY: "auto" }}>
              <p style={{ fontSize: 11, color: "var(--text3)", margin: "0 0 2px" }}>当前镜头</p>
              <p style={{ fontSize: 12, fontWeight: 500, margin: "0 0 10px" }}>镜头 02 · 属性</p>

              <p style={{ fontSize: 11, color: "var(--text3)", margin: "0 0 4px" }}>绘画提示词（实时合规检测）</p>
              <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} style={{ resize: "vertical", lineHeight: 1.5 }} />
              <button className="btn-primary" style={{ marginTop: 8 }} onClick={runCheck}>检测并生成此镜头</button>

              {result && (
                <div style={{ marginTop: 10, padding: 8, borderRadius: 8, border: "1px solid var(--border2)", background: "var(--panel)" }}>
                  {result.level === "red" && <div style={{ color: "var(--red-t)", fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}><AlertTriangle size={14} /> 红线 · 已硬拦截</div>}
                  {result.level === "yellow" && <div style={{ color: "var(--amber)", fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}><AlertTriangle size={14} /> 黄线 · 建议替换</div>}
                  {result.level === "pass" && <div style={{ color: "var(--green-t)", fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}><CheckCircle2 size={14} /> 通过 · 送算力生成</div>}
                  {result.hits.length > 0 && (
                    <div style={{ marginTop: 6, fontSize: 11, color: "var(--text2)" }}>
                      命中：{result.hits.map((h) => `${h.word}(${h.category})`).join("、")}
                    </div>
                  )}
                </div>
              )}

              <div style={{ borderTop: "1px solid var(--border)", marginTop: 12, paddingTop: 10 }}>
                {[["画风", "写实电影感"], ["模型", "Flux Kontext"], ["算力", "本地 ComfyUI"], ["时长", "5s"]].map(([k, v]) => (
                  <div key={k} style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 6 }}>
                    <span style={{ color: "var(--text3)" }}>{k}</span>
                    <span style={{ color: "var(--text2)" }}>{v}</span>
                  </div>
                ))}
              </div>
            </div>
          </>
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
