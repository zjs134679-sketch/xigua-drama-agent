import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Cpu, KeyRound, Loader2, Save, Wifi } from "lucide-react";
import {
  getLLMConfig,
  saveLLMConfig,
  testLLMConfig,
  type LLMConfig,
} from "../api/client";

// 国内主流 LLM，均走 OpenAI 兼容协议（base_url 末尾会自动拼 /chat/completions）
const PRESETS: { key: string; label: string; base_url: string; model: string }[] = [
  { key: "deepseek", label: "DeepSeek", base_url: "https://api.deepseek.com", model: "deepseek-chat" },
  { key: "qwen", label: "通义千问", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  { key: "doubao", label: "豆包", base_url: "https://ark.cn-beijing.volces.com/api/v3", model: "doubao-pro-32k" },
  { key: "kimi", label: "Kimi", base_url: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k" },
  { key: "zhipu", label: "智谱 GLM", base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4" },
];

export default function SettingsView() {
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
  }, []);

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
        api_key: apiKey.trim() || undefined, // 留空 = 不覆盖旧 key
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
        base_url: baseUrl.trim(),
        model: model.trim(),
        api_key: apiKey.trim() || undefined, // 留空 = 用已存 key
      });
      setTest({ ok: r.ok, message: r.message, ms: r.latency_ms, reply: r.reply });
    } catch (e) {
      setTest({ ok: false, message: e instanceof Error ? e.message : "测试失败" });
    } finally {
      setTesting(false);
    }
  };

  const labelStyle = { fontSize: 11, color: "var(--text3)", margin: "0 0 4px" } as const;

  return (
    <div style={{ flex: 1, minWidth: 0, overflowY: "auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
        <KeyRound size={15} color="var(--text2)" />
        <span style={{ fontSize: 12, fontWeight: 500 }}>设置</span>
        <span style={{ fontSize: 11, color: "var(--text3)" }}>编剧 / 分镜 Agent 的大模型</span>
      </div>

      <div style={{ maxWidth: 560, padding: 16, display: "flex", flexDirection: "column", gap: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Cpu size={14} color="var(--text2)" />
          <span style={{ fontSize: 13, fontWeight: 500 }}>大语言模型（LLM）</span>
          {cfg?.configured ? (
            <span className="pill" style={{ background: "rgba(43,178,76,0.16)", color: "var(--green-t)" }}>
              <CheckCircle2 size={12} /> 已配置{cfg.source === "env" ? "（环境变量）" : ""}
            </span>
          ) : (
            <span className="pill" style={{ background: "rgba(224,160,27,0.16)", color: "var(--amber)" }}>
              <AlertTriangle size={12} /> 未配置 · 编剧功能不可用
            </span>
          )}
        </div>

        <div>
          <p style={labelStyle}>服务商</p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {PRESETS.map((p) => (
              <button
                key={p.key}
                type="button"
                onClick={() => applyPreset(p.key)}
                className="pill"
                style={{
                  cursor: "pointer",
                  border: provider === p.key ? "1px solid var(--green)" : "1px solid var(--border2)",
                  color: provider === p.key ? "var(--green-t)" : "var(--text2)",
                  background: provider === p.key ? "rgba(43,178,76,0.12)" : "transparent",
                }}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <p style={labelStyle}>接口地址（Base URL）</p>
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.deepseek.com" />
        </div>

        <div>
          <p style={labelStyle}>模型名</p>
          <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="deepseek-chat" />
        </div>

        <div>
          <p style={labelStyle}>API Key{hasStoredKey ? "（已保存，留空则不修改）" : ""}</p>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={hasStoredKey ? "••••••••（已保存）" : "粘贴你的 API Key"}
            autoComplete="off"
          />
          <p style={{ fontSize: 10.5, color: "var(--text3)", margin: "5px 0 0" }}>
            Key 仅存本机后端，绝不回传前端、不出现在界面。
          </p>
        </div>

        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn-primary" style={{ width: "auto", padding: "7px 16px", opacity: saving ? 0.7 : 1 }} onClick={save} disabled={saving}>
            {saving ? <Loader2 size={14} className="spin" /> : <Save size={14} />} 保存
          </button>
          <button
            className="btn-secondary"
            style={{ width: "auto", padding: "7px 16px", opacity: testing ? 0.7 : 1 }}
            onClick={runTest}
            disabled={testing}
          >
            {testing ? <Loader2 size={14} className="spin" /> : <Wifi size={14} />} 测试连接
          </button>
        </div>

        {saved && (
          <div style={{ fontSize: 12, color: "var(--green-t)", display: "flex", gap: 6, alignItems: "center" }}>
            <CheckCircle2 size={14} /> 已保存
          </div>
        )}

        {test && (
          <div
            style={{
              padding: 10,
              borderRadius: 8,
              border: `1px solid ${test.ok ? "var(--green)" : "var(--red)"}`,
              background: "var(--panel)",
              fontSize: 12,
              color: test.ok ? "var(--green-t)" : "var(--red-t)",
            }}
          >
            <div style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 500 }}>
              {test.ok ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}
              {test.ok ? `连接成功${test.ms != null ? ` · ${test.ms}ms` : ""}` : "连接失败"}
            </div>
            <div style={{ marginTop: 4, color: "var(--text2)" }}>
              {test.ok ? `模型回复：${test.reply || "（空）"}` : test.message}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
