import { useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  KeyRound,
  Loader2,
  Save,
  Shield,
  BookOpen,
  Wifi,
} from "lucide-react";
import {
  backupProject,
  diagnoseCompute,
  getLLMConfig,
  getProjectManuals,
  getSkill,
  listSkills,
  refreshProjectMemory,
  saveLLMConfig,
  saveProjectManuals,
  saveSkill,
  testLLMConfig,
  type LLMConfig,
} from "../api/client";

// 国内主流 LLM，均走 OpenAI 兼容协议
const PRESETS: { key: string; label: string; base_url: string; model: string }[] = [
  { key: "deepseek", label: "DeepSeek", base_url: "https://api.deepseek.com", model: "deepseek-chat" },
  { key: "qwen", label: "通义千问", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  { key: "doubao", label: "豆包", base_url: "https://ark.cn-beijing.volces.com/api/v3", model: "doubao-pro-32k" },
  { key: "kimi", label: "Kimi", base_url: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k" },
  { key: "zhipu", label: "智谱 GLM", base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4" },
];

export default function SettingsView({
  dramaId,
  episodeId,
}: {
  dramaId?: number | null;
  episodeId?: number | null;
}) {
  const [cfg, setCfg] = useState<LLMConfig | null>(null);
  const [provider, setProvider] = useState("deepseek");
  const [baseUrl, setBaseUrl] = useState(PRESETS[0].base_url);
  const [model, setModel] = useState(PRESETS[0].model);
  const [apiKey, setApiKey] = useState("");
  const [hasStoredKey, setHasStoredKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saved, setSaved] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; message: string; ms?: number | null; reply?: string | null } | null>(null);

  const [director, setDirector] = useState("");
  const [visual, setVisual] = useState("");
  const [banned, setBanned] = useState("");
  const [manualMsg, setManualMsg] = useState("");
  const [diag, setDiag] = useState<Record<string, unknown> | null>(null);
  const [diagLoading, setDiagLoading] = useState(false);

  const [skills, setSkills] = useState<{ name: string; display_name: string; category: string }[]>([]);
  const [skillCat, setSkillCat] = useState("agent");
  const [skillName, setSkillName] = useState("");
  const [skillContent, setSkillContent] = useState("");
  const [skillMsg, setSkillMsg] = useState("");

  useEffect(() => {
    getLLMConfig()
      .then((c) => {
        setCfg(c);
        if (c.provider) setProvider(c.provider);
        if (c.base_url) setBaseUrl(c.base_url);
        if (c.model) setModel(c.model);
        setHasStoredKey(c.api_key_configured);
      })
      .catch(() => undefined);
    listSkills("agent")
      .then((list) => setSkills(list || []))
      .catch(() => setSkills([]));
  }, []);

  useEffect(() => {
    if (dramaId == null) return;
    getProjectManuals(dramaId)
      .then((m) => {
        setDirector(m.director_manual || "");
        setVisual(m.visual_manual || "");
        setBanned(m.banned_elements || "");
      })
      .catch(() => undefined);
  }, [dramaId]);

  const applyPreset = (key: string) => {
    setProvider(key);
    const p = PRESETS.find((x) => x.key === key);
    if (p) {
      setBaseUrl(p.base_url);
      setModel(p.model);
    }
    setTest(null);
    setSaved(false);
  };

  const save = async () => {
    setSaving(true);
    setSaved(false);
    setTest(null);
    try {
      const next = await saveLLMConfig({
        provider,
        base_url: baseUrl.trim(),
        model: model.trim(),
        api_key: apiKey.trim() || undefined,
      });
      setCfg(next);
      setHasStoredKey(next.api_key_configured);
      setApiKey("");
      setSaved(true);
    } catch (e) {
      setTest({ ok: false, message: e instanceof Error ? e.message : "保存失败" });
    } finally {
      setSaving(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      const r = await testLLMConfig({
        provider,
        base_url: baseUrl.trim(),
        model: model.trim(),
        api_key: apiKey.trim() || undefined,
      });
      setTest({ ok: r.ok, message: r.message, ms: r.latency_ms, reply: r.reply });
    } catch (e) {
      setTest({ ok: false, message: e instanceof Error ? e.message : "测试失败" });
    } finally {
      setTesting(false);
    }
  };

  const saveManuals = async () => {
    if (dramaId == null) {
      setManualMsg("请先打开项目");
      return;
    }
    try {
      await saveProjectManuals(dramaId, {
        director_manual: director,
        visual_manual: visual,
        banned_elements: banned,
        model_map: {
          video_test_quality: "test",
          video_final_quality: "final",
          video_duration: 5,
          notes: "4060 8G：出图/出片均 MiniMax H3",
        },
      });
      setManualMsg("手册与模型地图已保存");
    } catch (e) {
      setManualMsg(e instanceof Error ? e.message : "保存失败");
    }
  };

  const loadSkill = async (name: string) => {
    setSkillName(name);
    try {
      const s = await getSkill(skillCat, name);
      setSkillContent(s.raw || s.content || "");
      setSkillMsg("");
    } catch (e) {
      setSkillMsg(e instanceof Error ? e.message : "加载失败");
    }
  };

  return (
    <div className="feature-view" style={{ overflow: "auto", flex: 1 }}>
      <div className="feature-header">
        <div>
          <h2>设置</h2>
          <p>API 只写剧本/提示词 · 出图出片请用本机 ComfyUI（算力页）</p>
        </div>
      </div>

      <section className="settings-section" style={{ padding: 16, maxWidth: 720 }}>
        <h3 style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <KeyRound size={16} /> 语言模型 API（剧本 / 分镜 / 提示词）
        </h3>
        <p style={{ fontSize: 12, color: "var(--text3)", lineHeight: 1.5, margin: "0 0 8px" }}>
          这里的 Key 只给「写字」用：改编剧本、拆分镜、润色提示词。角色图、场景图、镜头视频全部走
          <strong> 本机 ComfyUI</strong>，不在这里填画图云 API。
        </p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "8px 0" }}>
          {PRESETS.map((p) => (
            <button key={p.key} type="button" className={`toolbar-button${provider === p.key ? " active" : ""}`} onClick={() => applyPreset(p.key)}>
              {p.label}
            </button>
          ))}
        </div>
        <label>Base URL</label>
        <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} style={{ width: "100%" }} />
        <label>Model</label>
        <input value={model} onChange={(e) => setModel(e.target.value)} style={{ width: "100%" }} />
        <label>API Key {hasStoredKey ? "（已配置，留空不改）" : ""}</label>
        <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} style={{ width: "100%" }} placeholder="sk-..." />
        <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
          <button type="button" className="btn-primary" disabled={saving} onClick={() => void save()}>
            {saving ? <Loader2 className="spin" size={14} /> : <Save size={14} />} 保存
          </button>
          <button type="button" className="btn-secondary" disabled={testing} onClick={() => void runTest()}>
            {testing ? <Loader2 className="spin" size={14} /> : <Wifi size={14} />} 测试
          </button>
        </div>
        {saved && (
          <div style={{ color: "var(--green)", marginTop: 8 }}>
            <CheckCircle2 size={14} /> 已保存 {cfg?.source}
          </div>
        )}
        {test && (
          <div style={{ color: test.ok ? "var(--green)" : "var(--amber)", marginTop: 8 }}>
            {test.ok ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />} {test.message}
            {test.ms != null && ` · ${test.ms}ms`}
          </div>
        )}
      </section>

      <section className="settings-section" style={{ padding: 16, maxWidth: 720 }}>
        <h3 style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <BookOpen size={16} /> 项目手册（当前项目 {dramaId ?? "未选"}）
        </h3>
        <p style={{ fontSize: 12, color: "var(--text3)" }}>写入后自动注入事件改编 / Agent 上下文（导演手册与视觉手册）。</p>
        <label>导演手册</label>
        <textarea rows={3} value={director} onChange={(e) => setDirector(e.target.value)} style={{ width: "100%" }} placeholder="节奏、情绪、禁忌桥段…" />
        <label>视觉手册</label>
        <textarea rows={3} value={visual} onChange={(e) => setVisual(e.target.value)} style={{ width: "100%" }} placeholder="画风、镜头语言、光影…" />
        <label>禁用元素</label>
        <textarea rows={2} value={banned} onChange={(e) => setBanned(e.target.value)} style={{ width: "100%" }} placeholder="不要出现的元素…" />
        <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
          <button type="button" className="btn-primary" onClick={() => void saveManuals()}>
            <Save size={14} /> 保存手册
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={dramaId == null}
            onClick={() =>
              dramaId != null &&
              void refreshProjectMemory(dramaId).then(() => setManualMsg("已刷新角色/场景记忆"))
            }
          >
            刷新角色记忆
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={dramaId == null}
            onClick={() =>
              dramaId != null &&
              void backupProject(dramaId).then((r) => setManualMsg(`备份完成 ${r.filename} (${r.bytes} bytes)`))
            }
          >
            备份项目 JSON
          </button>
        </div>
        {manualMsg && <div style={{ marginTop: 8, fontSize: 12, color: "var(--text2)" }}>{manualMsg}</div>}
        {episodeId != null && (
          <div style={{ marginTop: 6, fontSize: 11, color: "var(--text3)" }}>当前分集 #{episodeId}</div>
        )}
      </section>

      <section className="settings-section" style={{ padding: 16, maxWidth: 720 }}>
        <h3 style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Cpu size={16} /> ComfyUI 模型检测（出图 / 出片）
        </h3>
        <p style={{ fontSize: 12, color: "var(--text3)", margin: "0 0 8px" }}>
          角色、场景、分镜图、镜头视频都在 Comfy 里跑。请先启动 ComfyUI，再点诊断。
        </p>
        <button
          type="button"
          className="btn-secondary"
          disabled={diagLoading}
          onClick={() => {
            setDiagLoading(true);
            void diagnoseCompute()
              .then((d) => setDiag(d))
              .catch((e) => setDiag({ ok: false, message: e instanceof Error ? e.message : "失败" }))
              .finally(() => setDiagLoading(false));
          }}
        >
          {diagLoading ? <Loader2 className="spin" size={14} /> : <Shield size={14} />} 运行诊断
        </button>
        {diag && (
          <div style={{ marginTop: 10, fontSize: 12 }}>
            <div>
              {diag.online ? "✅ 在线" : "❌ 离线"} · {String(diag.message || "")}
            </div>
            <div style={{ color: "var(--text3)", marginTop: 4 }}>{String(diag.gpu_hint || "")}</div>
            <ul style={{ marginTop: 8, paddingLeft: 18 }}>
              {((diag.checks as { label: string; status: string; note: string }[]) || []).map((c) => (
                <li key={c.label}>
                  <strong>{c.label}</strong> [{c.status}] {c.note}
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <section className="settings-section" style={{ padding: 16, maxWidth: 720 }}>
        <h3>Agent Skill 在线编辑</h3>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
          {skills.map((s) => (
            <button key={s.name} type="button" className="toolbar-button" onClick={() => void loadSkill(s.name)}>
              {s.display_name || s.name}
            </button>
          ))}
        </div>
        {skillName && (
          <>
            <div style={{ fontSize: 12, color: "var(--text3)", marginBottom: 4 }}>
              {skillCat}/{skillName}
            </div>
            <textarea rows={12} value={skillContent} onChange={(e) => setSkillContent(e.target.value)} style={{ width: "100%", fontFamily: "monospace", fontSize: 12 }} />
            <button
              type="button"
              className="btn-primary"
              style={{ marginTop: 8 }}
              onClick={() =>
                void saveSkill(skillCat, skillName, skillContent)
                  .then(() => setSkillMsg("已保存"))
                  .catch((e) => setSkillMsg(e instanceof Error ? e.message : "失败"))
              }
            >
              <Save size={14} /> 保存 Skill
            </button>
            {skillMsg && <div style={{ marginTop: 6, fontSize: 12 }}>{skillMsg}</div>}
          </>
        )}
      </section>
    </div>
  );
}
