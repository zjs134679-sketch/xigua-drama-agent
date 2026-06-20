import { FormEvent, useEffect, useState } from "react";
import {
  Activity,
  Cloud,
  HardDrive,
  LoaderCircle,
  Plus,
  Power,
  RefreshCw,
  Save,
  Server,
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
}

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

export default function ComputeNodesView() {
  const [nodes, setNodes] = useState<ComputeNodeRecord[]>([]);
  const [form, setForm] = useState<NodeForm>(INITIAL_FORM);
  const [priorityDrafts, setPriorityDrafts] = useState<Record<number, number>>({});
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(null);

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

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const body: ComputeNodeInput = {
      name: form.name.trim(),
      type: form.type,
      base_url: form.baseUrl.trim(),
      priority: form.priority,
      is_active: form.active,
      ...(form.type === "remote_comfy" && form.token.trim() ? { token: form.token.trim() } : {}),
      ...(form.type === "cloud_api"
        ? {
            provider: form.provider,
            api_key: form.apiKey.trim(),
            model: form.model.trim(),
            capabilities: form.provider === "seedance" ? "video" : "image",
          }
        : {}),
    };
    await runAction("create", async () => {
      await createComputeNode(body);
      setForm(INITIAL_FORM);
      setNotice({ error: false, text: "节点已新增" });
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
      setNotice({ error: false, text: `${node.name} 已删除` });
    });

  const creatingDisabled =
    !form.name.trim() ||
    !form.baseUrl.trim() ||
    (form.type === "cloud_api" && (!form.apiKey.trim() || !form.model.trim()));

  return (
    <div className="feature-view compute-view">
      <div className="feature-header">
        <div><h2>算力节点</h2><p>本地优先，远程主机与云 API 作为弹性补充</p></div>
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
            <article className={`compute-row${node.is_active ? "" : " disabled"}`} key={node.id}>
              <div className="compute-name">
                <NodeTypeIcon type={node.type} />
                <span><strong>{node.name}</strong><small>{TYPE_LABELS[node.type]}</small></span>
              </div>
              <div className="compute-endpoint">
                <span title={node.base_url}>{node.base_url}</span>
                <small>
                  {node.type === "cloud_api"
                    ? `${node.provider || "未配置 provider"} · ${node.model || "未配置模型"} · Key ${node.api_key_configured ? "已配置" : "未配置"}`
                    : node.type === "remote_comfy" && node.token_configured ? "Token 已配置" : ""}
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

        <aside className="feature-aside">
          <h3><Plus size={15} /> 新增节点</h3>
          <form onSubmit={(event) => void submit(event)}>
            <label>节点名称</label>
            <input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="例如：本地工作站" />
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
                <label>模型</label>
                <input value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} />
              </>
            )}
            <label>优先级（数字越大越优先）</label>
            <input type="number" value={form.priority} onChange={(event) => setForm({ ...form, priority: Number(event.target.value) })} />
            <label className="compute-checkbox">
              <input type="checkbox" checked={form.active} onChange={(event) => setForm({ ...form, active: event.target.checked })} />
              创建后立即启用
            </label>
            <button className="btn-primary" disabled={creatingDisabled || busy === "create"}>
              {busy === "create" ? <LoaderCircle className="spin" size={14} /> : <Plus size={14} />} 新增节点
            </button>
          </form>
        </aside>
      </div>
    </div>
  );
}
