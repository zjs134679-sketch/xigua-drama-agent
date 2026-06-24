import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";
import {
  Activity,
  Cloud,
  FileCode2,
  HardDrive,
  LoaderCircle,
  Plus,
  Power,
  RefreshCw,
  Save,
  Server,
  Settings2,
  Trash2,
} from "lucide-react";
import {
  createComputeNode,
  deleteComputeNode,
  listComputeNodes,
  testComputeNode,
  updateComputeNode,
  type ComputeNodeInput,
  type ComputeNodeRecord,
  type ComputeNodeType,
} from "../api/client";

const CLOUD_DEFAULTS = {
  wan: { baseUrl: "https://dashscope.aliyuncs.com", model: "wanx-v1" },
  seedance: {
    baseUrl: "https://ark.cn-beijing.volces.com",
    model: "doubao-seedance-1-0-pro-250528",
  },
};

const MODEL_MODE_OPTIONS = [
  { key: "image", label: "图像模型" },
  { key: "t2i", label: "文生图" },
  { key: "i2i", label: "单图" },
  { key: "multiref", label: "多图参考" },
  { key: "video", label: "视频" },
];

interface ModelSettingsDraft {
  width: string;
  height: string;
  steps: string;
  cfg: string;
  model: string;
  ckpt_name: string;
  unet_name: string;
  clip1: string;
  clip2: string;
  clip_name: string;
  clip_type: string;
  vae_name: string;
  weight_dtype: string;
  workflow: string;
  workflow_t2i: string;
  workflow_refs: string;
  modes: string[];
}

interface NodeForm {
  name: string;
  type: ComputeNodeType;
  baseUrl: string;
  token: string;
  provider: keyof typeof CLOUD_DEFAULTS;
  apiKey: string;
  model: string;
  priority: number;
  active: boolean;
  modelSettings: ModelSettingsDraft;
  adapterCode: string;
  adapterFilename: string;
}

const DEFAULT_MODEL_SETTINGS: ModelSettingsDraft = {
  width: "768",
  height: "432",
  steps: "6",
  cfg: "1",
  model: "",
  ckpt_name: "",
  unet_name: "",
  clip1: "",
  clip2: "",
  clip_name: "",
  clip_type: "",
  vae_name: "",
  weight_dtype: "",
  workflow: "",
  workflow_t2i: "",
  workflow_refs: "",
  modes: ["image", "t2i", "i2i", "multiref"],
};

const INITIAL_FORM: NodeForm = {
  name: "",
  type: "local_comfy",
  baseUrl: "http://127.0.0.1:8188",
  token: "",
  provider: "wan",
  apiKey: "",
  model: CLOUD_DEFAULTS.wan.model,
  priority: 100,
  active: true,
  modelSettings: DEFAULT_MODEL_SETTINGS,
  adapterCode: "",
  adapterFilename: "",
};

const TYPE_LABELS: Record<ComputeNodeType, string> = {
  local_comfy: "本地 ComfyUI",
  remote_comfy: "远程 ComfyUI",
  cloud_api: "云 API",
};

function NodeTypeIcon({ type }: { type: ComputeNodeType }) {
  if (type === "local_comfy") return <HardDrive size={16} />;
  if (type === "remote_comfy") return <Server size={16} />;
  return <Cloud size={16} />;
}

function stringValue(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

function settingsFromRecord(settings: Record<string, unknown> | null | undefined): ModelSettingsDraft {
  const modes = Array.isArray(settings?.modes)
    ? settings?.modes.filter((item): item is string => typeof item === "string")
    : DEFAULT_MODEL_SETTINGS.modes;
  return {
    ...DEFAULT_MODEL_SETTINGS,
    width: stringValue(settings?.width) || DEFAULT_MODEL_SETTINGS.width,
    height: stringValue(settings?.height) || DEFAULT_MODEL_SETTINGS.height,
    steps: stringValue(settings?.steps) || DEFAULT_MODEL_SETTINGS.steps,
    cfg: stringValue(settings?.cfg) || DEFAULT_MODEL_SETTINGS.cfg,
    model: stringValue(settings?.model),
    ckpt_name: stringValue(settings?.ckpt_name),
    unet_name: stringValue(settings?.unet_name),
    clip1: stringValue(settings?.clip1),
    clip2: stringValue(settings?.clip2),
    clip_name: stringValue(settings?.clip_name),
    clip_type: stringValue(settings?.clip_type),
    vae_name: stringValue(settings?.vae_name),
    weight_dtype: stringValue(settings?.weight_dtype),
    workflow: stringValue(settings?.workflow),
    workflow_t2i: stringValue(settings?.workflow_t2i),
    workflow_refs: stringValue(settings?.workflow_refs),
    modes: modes.length ? modes : DEFAULT_MODEL_SETTINGS.modes,
  };
}

function compactSettings(draft: ModelSettingsDraft): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const key of ["width", "height", "steps"] as const) {
    const value = Number(draft[key]);
    if (Number.isFinite(value) && value > 0) result[key] = Math.round(value);
  }
  const cfg = Number(draft.cfg);
  if (Number.isFinite(cfg) && cfg > 0) result.cfg = cfg;
  for (const key of [
    "model",
    "ckpt_name",
    "unet_name",
    "clip1",
    "clip2",
    "clip_name",
    "clip_type",
    "vae_name",
    "weight_dtype",
    "workflow",
    "workflow_t2i",
    "workflow_refs",
  ] as const) {
    const value = draft[key].trim();
    if (value) result[key] = value;
  }
  result.modes = draft.modes;
  return result;
}

function capabilitiesFromModes(modes: string[]): string {
  if (modes.includes("video")) return "image,video";
  return "image";
}

function defaultNodeName(draft: NodeForm): string {
  const configuredModel =
    draft.modelSettings.model.trim() ||
    draft.modelSettings.unet_name.trim() ||
    draft.modelSettings.ckpt_name.trim() ||
    draft.model.trim();
  if (configuredModel) return configuredModel;
  return TYPE_LABELS[draft.type];
}

async function readTsFile(file: File): Promise<{ filename: string; code: string }> {
  const code = await file.text();
  return { filename: file.name, code };
}

function ModelSettingsEditor({
  value,
  onChange,
  adapterCode,
  adapterFilename,
  onAdapterChange,
}: {
  value: ModelSettingsDraft;
  onChange: (next: ModelSettingsDraft) => void;
  adapterCode: string;
  adapterFilename: string;
  onAdapterChange: (code: string, filename?: string) => void;
}) {
  const patch = (changes: Partial<ModelSettingsDraft>) => onChange({ ...value, ...changes });
  const toggleMode = (mode: string) => {
    const exists = value.modes.includes(mode);
    patch({ modes: exists ? value.modes.filter((item) => item !== mode) : [...value.modes, mode] });
  };
  const importAdapter = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    const imported = await readTsFile(file);
    onAdapterChange(imported.code, imported.filename);
  };

  return (
    <div className="node-config-panel">
      <div className="node-config-block">
        <div className="node-config-title">
          <Settings2 size={14} /> 模型参数
        </div>
        <div className="node-param-grid">
          <label>Width * 必填<input value={value.width} onChange={(event) => patch({ width: event.target.value })} /></label>
          <label>Height * 必填<input value={value.height} onChange={(event) => patch({ height: event.target.value })} /></label>
          <label>Steps * 必填<input value={value.steps} onChange={(event) => patch({ steps: event.target.value })} /></label>
          <label>CFG * 必填<input value={value.cfg} onChange={(event) => patch({ cfg: event.target.value })} /></label>
        </div>
        <details>
          <summary>选填项</summary>
          <div className="node-param-grid">
            <label>模型名 / model<input value={value.model} onChange={(event) => patch({ model: event.target.value })} placeholder="例如 flux-2-klein-9b-fp8.safetensors" /></label>
            <label>Checkpoint<input value={value.ckpt_name} onChange={(event) => patch({ ckpt_name: event.target.value })} /></label>
            <label>Unet<input value={value.unet_name} onChange={(event) => patch({ unet_name: event.target.value })} /></label>
            <label>单 CLIP<input value={value.clip_name} onChange={(event) => patch({ clip_name: event.target.value })} placeholder="例如 qwen_3_8b_fp8mixed.safetensors" /></label>
            <label>CLIP 类型<input value={value.clip_type} onChange={(event) => patch({ clip_type: event.target.value })} placeholder="例如 flux2" /></label>
            <label>CLIP1<input value={value.clip1} onChange={(event) => patch({ clip1: event.target.value })} /></label>
            <label>CLIP2<input value={value.clip2} onChange={(event) => patch({ clip2: event.target.value })} /></label>
            <label>VAE<input value={value.vae_name} onChange={(event) => patch({ vae_name: event.target.value })} /></label>
            <label>权重精度<input value={value.weight_dtype} onChange={(event) => patch({ weight_dtype: event.target.value })} placeholder="default / fp8_e4m3fn" /></label>
            <label>默认工作流<input value={value.workflow} onChange={(event) => patch({ workflow: event.target.value })} placeholder="例如 flux-t2i.api.json" /></label>
            <label>文生图工作流<input value={value.workflow_t2i} onChange={(event) => patch({ workflow_t2i: event.target.value })} placeholder="无参考图时使用" /></label>
            <label>参考图工作流<input value={value.workflow_refs} onChange={(event) => patch({ workflow_refs: event.target.value })} placeholder="有参考图时使用，例如 flux2-klein-triref.api.json" /></label>
          </div>
        </details>
      </div>

      <div className="node-config-block">
        <div className="node-config-title">模型设置</div>
        <div className="node-mode-tags">
          {MODEL_MODE_OPTIONS.map((mode) => (
            <button
              type="button"
              key={mode.key}
              className={value.modes.includes(mode.key) ? "active" : ""}
              onClick={() => toggleMode(mode.key)}
            >
              {mode.label}
            </button>
          ))}
        </div>
      </div>

      <div className="node-config-block">
        <div className="node-config-title">
          <FileCode2 size={14} /> TS 适配器
          {adapterFilename && <small>{adapterFilename}</small>}
        </div>
        <label className="node-file-import">
          导入 ts 文件
          <input type="file" accept=".ts,.tsx,.js,.mjs,text/plain" onChange={(event) => void importAdapter(event)} />
        </label>
        <textarea
          className="adapter-code-editor"
          value={adapterCode}
          onChange={(event) => onAdapterChange(event.target.value)}
          placeholder={"可粘贴/导入 provider ts 代码；当前会保存到节点配置，后续生成器可读取。"}
        />
      </div>
    </div>
  );
}

export default function ComputeNodesView() {
  const [nodes, setNodes] = useState<ComputeNodeRecord[]>([]);
  const [form, setForm] = useState<NodeForm>(INITIAL_FORM);
  const [priorityDrafts, setPriorityDrafts] = useState<Record<number, number>>({});
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<NodeForm | null>(null);

  const editingNode = useMemo(
    () => nodes.find((node) => node.id === editingId) ?? null,
    [editingId, nodes],
  );

  const load = async () => {
    try {
      const rows = await listComputeNodes();
      setNodes(rows);
      setPriorityDrafts(Object.fromEntries(rows.map((node) => [node.id, node.priority])));
    } catch (error) {
      setNotice({ error: true, text: error instanceof Error ? error.message : "节点列表加载失败" });
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const runAction = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    setNotice(null);
    try {
      await action();
      await load();
    } catch (error) {
      setNotice({ error: true, text: error instanceof Error ? error.message : "节点操作失败" });
    } finally {
      setBusy("");
    }
  };

  const changeType = (type: ComputeNodeType) => {
    setForm((current) => ({
      ...current,
      type,
      baseUrl:
        type === "local_comfy"
          ? "http://127.0.0.1:8188"
          : type === "cloud_api"
            ? CLOUD_DEFAULTS[current.provider].baseUrl
            : "",
      model: type === "cloud_api" ? CLOUD_DEFAULTS[current.provider].model : "",
    }));
  };

  const changeProvider = (provider: keyof typeof CLOUD_DEFAULTS) => {
    const defaults = CLOUD_DEFAULTS[provider];
    setForm((current) => ({ ...current, provider, baseUrl: defaults.baseUrl, model: defaults.model }));
  };

  const bodyFromForm = (draft: NodeForm): ComputeNodeInput => {
    const modelSettings = compactSettings(draft.modelSettings);
    return {
      name: draft.name.trim() || defaultNodeName(draft),
      type: draft.type,
      base_url: draft.baseUrl.trim(),
      priority: draft.priority,
      is_active: draft.active,
      capabilities: capabilitiesFromModes(draft.modelSettings.modes),
      model_settings: modelSettings,
      adapter_code: draft.adapterCode,
      adapter_filename: draft.adapterFilename,
      ...(draft.type === "remote_comfy" && draft.token.trim() ? { token: draft.token.trim() } : {}),
      ...(draft.type === "cloud_api"
        ? {
            provider: draft.provider,
            api_key: draft.apiKey.trim(),
            model: draft.model.trim(),
          }
        : {}),
    };
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    await runAction("create", async () => {
      await createComputeNode(bodyFromForm(form));
      setForm(INITIAL_FORM);
      setNotice({ error: false, text: "节点已新增，模型配置已保存" });
    });
  };

  const test = (node: ComputeNodeRecord) =>
    runAction(`test-${node.id}`, async () => {
      const result = await testComputeNode(node.id);
      setNotice({
        error: !result.online,
        text: result.online ? `${node.name} 连接正常` : `${node.name}：${result.error || "连接失败"}`,
      });
    });

  const toggle = (node: ComputeNodeRecord) =>
    runAction(`toggle-${node.id}`, async () => {
      await updateComputeNode(node.id, { is_active: !node.is_active });
      setNotice({ error: false, text: `${node.name} 已${node.is_active ? "停用" : "启用"}` });
    });

  const savePriority = (node: ComputeNodeRecord) =>
    runAction(`priority-${node.id}`, async () => {
      await updateComputeNode(node.id, { priority: priorityDrafts[node.id] ?? node.priority });
      setNotice({ error: false, text: `${node.name} 优先级已更新` });
    });

  const remove = (node: ComputeNodeRecord) =>
    runAction(`delete-${node.id}`, async () => {
      await deleteComputeNode(node.id);
      if (editingId === node.id) {
        setEditingId(null);
        setEditForm(null);
      }
      setNotice({ error: false, text: `${node.name} 已删除` });
    });

  const startEdit = (node: ComputeNodeRecord) => {
    setEditingId(node.id);
    setEditForm({
      name: node.name,
      type: node.type,
      baseUrl: node.base_url,
      token: "",
      provider: (node.provider === "seedance" ? "seedance" : "wan") as keyof typeof CLOUD_DEFAULTS,
      apiKey: "",
      model: node.model || "",
      priority: node.priority,
      active: node.is_active,
      modelSettings: settingsFromRecord(node.model_settings),
      adapterCode: node.adapter_code || "",
      adapterFilename: node.adapter_filename || "",
    });
  };

  const saveEdit = async (event: FormEvent) => {
    event.preventDefault();
    if (!editingNode || !editForm) return;
    await runAction(`edit-${editingNode.id}`, async () => {
      await updateComputeNode(editingNode.id, bodyFromForm(editForm));
      setNotice({ error: false, text: `${editForm.name || editingNode.name} 配置已保存` });
    });
  };

  const creatingDisabled =
    !form.baseUrl.trim() ||
    (form.type === "cloud_api" && (!form.apiKey.trim() || !form.model.trim()));

  return (
    <div className="feature-view compute-view">
      <div className="feature-header">
        <div><h2>算力节点</h2><p>配置模型服务、工作流参数和 TS 适配器</p></div>
        <span className="pill" style={{ border: "1px solid var(--border2)", color: "var(--text2)" }}>
          <Activity size={13} /> {nodes.filter((node) => node.is_active).length} 个启用
        </span>
      </div>
      {notice && <div className={`feature-notice${notice.error ? " error" : ""}`}>{notice.text}</div>}
      <div className="feature-body two-column compute-layout">
        <section className="compute-list">
          <div className="compute-table-head">
            <span>节点</span><span>地址 / 模型</span><span>优先级</span><span>状态</span><span>操作</span>
          </div>
          {nodes.map((node) => (
            <article className={`compute-row${node.is_active ? "" : " disabled"}${editingId === node.id ? " selected" : ""}`} key={node.id}>
              <div className="compute-name">
                <NodeTypeIcon type={node.type} />
                <span>
                  <strong>{node.name}</strong>
                  <small>{TYPE_LABELS[node.type]}{node.adapter_configured ? " · TS已导入" : ""}</small>
                </span>
              </div>
              <div className="compute-endpoint">
                <span title={node.base_url}>{node.base_url}</span>
                <small>
                  {node.type === "cloud_api"
                    ? `${node.provider || "未配置 provider"} · ${node.model || "未配置模型"} · Key ${node.api_key_configured ? "已配置" : "未配置"}`
                    : `${stringValue(node.model_settings?.model) || stringValue(node.model_settings?.unet_name) || stringValue(node.model_settings?.ckpt_name) || "未配置模型"} · ${stringValue(node.model_settings?.width) || "?"}×${stringValue(node.model_settings?.height) || "?"}`}
                </small>
              </div>
              <div className="priority-editor">
                <input
                  type="number"
                  value={priorityDrafts[node.id] ?? node.priority}
                  onChange={(event) => setPriorityDrafts((current) => ({
                    ...current,
                    [node.id]: Number(event.target.value),
                  }))}
                  aria-label={`${node.name} 优先级`}
                />
                <button
                  type="button"
                  title="保存优先级"
                  disabled={busy === `priority-${node.id}`}
                  onClick={() => void savePriority(node)}
                ><Save size={13} /></button>
              </div>
              <div className={`node-status ${node.last_status || "unknown"}`}>
                <i />{node.last_status === "online" ? "在线" : node.last_status === "offline" ? "离线" : "未测试"}
              </div>
              <div className="compute-actions">
                <button type="button" title="测试连接" disabled={busy === `test-${node.id}`} onClick={() => void test(node)}>
                  {busy === `test-${node.id}` ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}
                </button>
                <button type="button" title="编辑配置" onClick={() => startEdit(node)}>
                  <Settings2 size={14} />
                </button>
                <button type="button" title={node.is_active ? "停用" : "启用"} onClick={() => void toggle(node)}>
                  <Power size={14} />
                </button>
                <button className="danger" type="button" title="删除" onClick={() => void remove(node)}>
                  <Trash2 size={14} />
                </button>
              </div>
            </article>
          ))}
          {!nodes.length && <div className="empty-state"><Server size={18} /> 暂无算力节点，请在右侧新增。</div>}
        </section>

        <aside className="feature-aside compute-config-aside">
          {editingNode && editForm ? (
            <>
              <h3><Settings2 size={15} /> 编辑节点</h3>
              <form onSubmit={(event) => void saveEdit(event)}>
                <label>节点名称</label>
                <input value={editForm.name} onChange={(event) => setEditForm({ ...editForm, name: event.target.value })} />
                <label>Base URL</label>
                <input value={editForm.baseUrl} onChange={(event) => setEditForm({ ...editForm, baseUrl: event.target.value })} />
                <label>优先级</label>
                <input type="number" value={editForm.priority} onChange={(event) => setEditForm({ ...editForm, priority: Number(event.target.value) })} />
                <ModelSettingsEditor
                  value={editForm.modelSettings}
                  onChange={(modelSettings) => setEditForm({ ...editForm, modelSettings })}
                  adapterCode={editForm.adapterCode}
                  adapterFilename={editForm.adapterFilename}
                  onAdapterChange={(adapterCode, adapterFilename) => setEditForm({
                    ...editForm,
                    adapterCode,
                    adapterFilename: adapterFilename ?? editForm.adapterFilename,
                  })}
                />
                <label className="compute-checkbox">
                  <input type="checkbox" checked={editForm.active} onChange={(event) => setEditForm({ ...editForm, active: event.target.checked })} />
                  启用该节点
                </label>
                <button className="btn-primary" disabled={busy === `edit-${editingNode.id}`}>
                  {busy === `edit-${editingNode.id}` ? <LoaderCircle className="spin" size={14} /> : <Save size={14} />} 保存配置
                </button>
                <button className="btn-secondary" type="button" onClick={() => { setEditingId(null); setEditForm(null); }}>
                  返回新增节点
                </button>
              </form>
            </>
          ) : (
            <>
              <h3><Settings2 size={15} /> 节点配置</h3>
              <form onSubmit={(event) => void submit(event)}>
                <label>节点名称（可不填，自动用模型名）</label>
                <input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="例如：本地工作站 / FLUX2 Klein" />
                <label>节点类型</label>
                <select value={form.type} onChange={(event) => changeType(event.target.value as ComputeNodeType)}>
                  <option value="local_comfy">本地 ComfyUI</option>
                  <option value="remote_comfy">远程 ComfyUI</option>
                  <option value="cloud_api">云 API</option>
                </select>
                {form.type === "cloud_api" && (
                  <>
                    <label>Provider</label>
                    <select value={form.provider} onChange={(event) => changeProvider(event.target.value as keyof typeof CLOUD_DEFAULTS)}>
                      <option value="wan">WAN（通义万相）</option>
                      <option value="seedance">Seedance（火山）</option>
                    </select>
                  </>
                )}
                <label>Base URL</label>
                <input value={form.baseUrl} onChange={(event) => setForm({ ...form, baseUrl: event.target.value })} placeholder="https://host.example.com" />
                {form.type === "remote_comfy" && (
                  <><label>Token</label><input type="password" value={form.token} onChange={(event) => setForm({ ...form, token: event.target.value })} autoComplete="new-password" /></>
                )}
                {form.type === "cloud_api" && (
                  <>
                    <label>API Key</label>
                    <input type="password" value={form.apiKey} onChange={(event) => setForm({ ...form, apiKey: event.target.value })} autoComplete="new-password" />
                    <label>云模型</label>
                    <input value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} />
                  </>
                )}
                <label>优先级（数字越大越优先）</label>
                <input type="number" value={form.priority} onChange={(event) => setForm({ ...form, priority: Number(event.target.value) })} />
                <ModelSettingsEditor
                  value={form.modelSettings}
                  onChange={(modelSettings) => setForm({ ...form, modelSettings })}
                  adapterCode={form.adapterCode}
                  adapterFilename={form.adapterFilename}
                  onAdapterChange={(adapterCode, adapterFilename) => setForm({
                    ...form,
                    adapterCode,
                    adapterFilename: adapterFilename ?? form.adapterFilename,
                  })}
                />
                <label className="compute-checkbox">
                  <input type="checkbox" checked={form.active} onChange={(event) => setForm({ ...form, active: event.target.checked })} />
                  创建后立即启用
                </label>
                <button className="btn-primary" type="submit" disabled={creatingDisabled || busy === "create"}>
                  {busy === "create" ? <LoaderCircle className="spin" size={14} /> : <Plus size={14} />} 新增节点
                </button>
              </form>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
