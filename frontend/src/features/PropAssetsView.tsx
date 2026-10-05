import { useEffect, useState } from "react";
import { AlertTriangle, Image, LoaderCircle, Package, Save, Sparkles, Wand2 } from "lucide-react";
import {
  extractFromEpisode,
  generatePropAsset,
  listArtStyles,
  listComputeNodes,
  listProjects,
  listProps,
  mediaDisplayUrl,
  polishPrompt,
  updatePropPrompt,
  type ArtStyle,
  type AssetResolution,
  type ComputeNodeRecord,
  type Project,
  type PropAsset,
} from "../api/client";
import BatchBar from "../components/BatchBar";
import AssetGenerationControls from "../components/AssetGenerationControls";
import AssetHistoryStrip from "../components/AssetHistoryStrip";
import AdditionalInstructionField from "../components/AdditionalInstructionField";
import ImageLightbox from "../components/ImageLightbox";
import MediaUploadButton from "../components/MediaUploadButton";
import { useBatchRun, type BatchOutcome } from "../components/useBatchRun";
import { useSelection } from "../components/useSelection";
import { followProjectStyleLabel, resolveStyleName } from "./styleHelpers";

export default function PropAssetsView({
  currentDramaId,
  currentEpisodeId,
  username = "local",
}: {
  currentDramaId?: number | null;
  currentEpisodeId?: number | null;
  username?: string;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [props, setProps] = useState<PropAsset[]>([]);
  const [styles, setStyles] = useState<ArtStyle[]>([]);
  const [styleId, setStyleId] = useState<number | undefined>();
  const [nodes, setNodes] = useState<ComputeNodeRecord[]>([]);
  const [resolutions, setResolutions] = useState<Record<number, AssetResolution>>({});
  const [stepsById, setStepsById] = useState<Record<number, number>>({});
  const [nodeIds, setNodeIds] = useState<Record<number, number | undefined>>({});
  const [generating, setGenerating] = useState<number | null>(null);
  const [extracting, setExtracting] = useState(false);
  const [saving, setSaving] = useState<number | null>(null);
  const [polishing, setPolishing] = useState<number | null>(null);
  const [prompts, setPrompts] = useState<Record<number, string>>({});
  const [notice, setNotice] = useState("");
  const [extra, setExtra] = useState("");
  const [preview, setPreview] = useState<string | null>(null);
  const sel = useSelection();
  const batch = useBatchRun();

  useEffect(() => {
    listProjects().then((rows) => {
      setProjects(rows);
      if (rows.length) {
        const preferred = currentDramaId != null && rows.some((row) => row.id === currentDramaId)
          ? currentDramaId
          : rows[0].id;
        setProjectId(preferred);
      }
    }).catch((error: Error) => setNotice(error.message));
    listArtStyles().then(setStyles).catch(() => undefined);
    listComputeNodes().then(setNodes).catch(() => undefined);
    setStyleId(undefined);
  }, [currentDramaId]);

  const activeProject = projects.find((p) => p.id === projectId) ?? null;
  const effectiveStyleLabel = resolveStyleName(styles, styleId, activeProject);

  const loadProps = (id: number) => listProps(id).then((rows) => {
    setProps(rows);
    setPrompts((previous) => {
      const next = { ...previous };
      rows.forEach((prop) => {
        if (next[prop.id] === undefined) next[prop.id] = prop.prompt ?? "";
      });
      return next;
    });
  }).catch((error: Error) => setNotice(error.message));

  useEffect(() => {
    if (projectId != null) void loadProps(projectId);
    sel.clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const savePrompt = async (prop: PropAsset) => {
    setSaving(prop.id);
    setNotice("");
    try {
      await updatePropPrompt(prop.id, prompts[prop.id] ?? "");
      setNotice(`已保存「${prop.name}」的提示词`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(null);
    }
  };

  const polishPromptForProp = async (prop: PropAsset) => {
    setPolishing(prop.id);
    setNotice("");
    try {
      const result = await polishPrompt({
        asset_type: "prop",
        prompt: prompts[prop.id] ?? "",
        // 只给物件身份，不把描述里的场景句塞给润色模型当「上下文续写」
        context: [prop.name, prop.type].filter(Boolean).join("，"),
      });
      const polished = result.polished || "";
      setPrompts((previous) => ({ ...previous, [prop.id]: polished }));
      // 同步落库，避免刷新后又回到带环境的旧 prompt
      try {
        await updatePropPrompt(prop.id, polished);
      } catch {
        /* 生成仍可用本地 polished */
      }
      setNotice(
        polished.includes("影棚") || polished.includes("浅灰")
          ? `已润色「${prop.name}」：已固定影棚底板、去掉环境句`
          : `已润色「${prop.name}」的提示词`,
      );
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "润色失败");
    } finally {
      setPolishing(null);
    }
  };

  const generateCore = async (prop: PropAsset): Promise<BatchOutcome> => {
    setGenerating(prop.id);
    try {
      const result = await generatePropAsset({
        prop_id: prop.id,
        prompt: prompts[prop.id] ?? undefined,
        art_style_id: styleId,
        username,
        node_id: nodeIds[prop.id],
        resolution: resolutions[prop.id] ?? "square_1024x1024",
        steps: stepsById[prop.id] ?? 32,
        extra: extra.trim() || undefined,
      });
      return { ok: !result.blocked, message: result.blocked ? result.message ?? "提示词触发红线" : undefined };
    } catch (error) {
      const message = error instanceof Error ? error.message : "生成失败";
      return { ok: false, message, banned: message.includes("封") };
    } finally {
      setGenerating(null);
    }
  };

  const generate = async (prop: PropAsset) => {
    setNotice("");
    const outcome = await generateCore(prop);
    setNotice(outcome.ok ? "道具图生成完成。" : outcome.message ?? "生成失败");
    if (projectId != null) await loadProps(projectId);
  };

  const runBatch = async () => {
    const queue = props.filter((prop) => sel.selected.has(prop.id));
    if (!queue.length) return;
    setNotice("");
    const byId = new Map(props.map((prop) => [prop.id, prop]));
    const result = await batch.run(queue.map((prop) => prop.id), (id) => generateCore(byId.get(id)!));
    if (projectId != null) await loadProps(projectId);
    if (!result) return;
    const succeeded = result.done - result.failed;
    if (result.banned) setNotice("账号已被封禁，已停止批量生成。");
    else if (result.stopped) setNotice(`已停止：成功 ${succeeded}/${result.total}，失败 ${result.failed}。`);
    else setNotice(`批量完成：成功 ${succeeded}、失败 ${result.failed}，共 ${result.total}。${result.lastMessage ? `（最后一条：${result.lastMessage}）` : ""}`);
  };

  const extract = async () => {
    if (currentEpisodeId == null) {
      setNotice("请先在「项目」里进入一个分集，再从它的剧本提取道具。");
      return;
    }
    setExtracting(true);
    setNotice("");
    try {
      const result = await extractFromEpisode(currentEpisodeId, username);
      if (result.status === 200 && result.new) {
        setNotice(`提取完成：道具 +${result.new.props}、角色 +${result.new.characters}、场景 +${result.new.scenes}`);
        if (projectId != null) await loadProps(projectId);
      } else {
        setNotice(result.message || "提取失败");
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "提取失败");
    } finally {
      setExtracting(false);
    }
  };

  const allIds = props.map((prop) => prop.id);
  const ungeneratedIds = props.filter((prop) => !prop.image_url).map((prop) => prop.id);
  const progressText = batch.progress
    ? `生成中 ${batch.progress.done}/${batch.progress.total}${batch.progress.failed ? `（失败 ${batch.progress.failed}）` : ""}`
    : null;

  return (
    <div className="feature-view">
      <div className="feature-header">
        <div>
          <h2>道具资产</h2>
          <p>从剧本提取道具，生成可复用参考图 · 当前出图画风：<strong style={{ color: "var(--green-t)" }}>{effectiveStyleLabel}</strong></p>
        </div>
        <div className="feature-filters">
          <button className="btn-primary" style={{ width: "auto", padding: "6px 14px" }} disabled={extracting} onClick={extract} title="从当前分集的剧本/原文中提取角色、场景、道具">
            {extracting ? <LoaderCircle className="spin" size={14} /> : <Wand2 size={14} />} AI 提取道具
          </button>
          <select
            value={projectId ?? ""}
            onChange={(event) => {
              setProjectId(Number(event.target.value));
              setStyleId(undefined);
            }}
          >
            {!projects.length && <option value="">暂无项目</option>}
            {projects.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
          </select>
          <select
            value={styleId ?? ""}
            onChange={(event) => setStyleId(event.target.value ? Number(event.target.value) : undefined)}
            title="默认跟随项目画风"
          >
            <option value="">{followProjectStyleLabel(activeProject)}</option>
            {styles.map((style) => <option key={style.id} value={style.id}>{style.name}（仅本次出图）</option>)}
          </select>
        </div>
      </div>
      <AdditionalInstructionField value={extra} onChange={setExtra} disabled={batch.running} />
      <BatchBar
        total={props.length}
        ungeneratedCount={ungeneratedIds.length}
        selectedCount={sel.selected.size}
        onSelectAll={() => sel.replace(allIds)}
        onSelectUngenerated={() => sel.replace(ungeneratedIds)}
        onInvert={() => sel.invert(allIds)}
        onClear={() => sel.clear()}
        onRun={runBatch}
        onStop={batch.stop}
        running={batch.running}
        progressText={progressText}
        runLabel="批量生成道具图"
      />
      {notice && <div className="feature-notice"><AlertTriangle size={14} /> {notice}</div>}
      <div className="feature-body">
        <div className="asset-grid character-grid">
          {props.map((prop) => {
            const picked = sel.selected.has(prop.id);
            return (
              <article className="asset-card" key={prop.id} style={picked ? { outline: "2px solid var(--green)", outlineOffset: -1 } : undefined}>
                <div className="asset-preview" style={{ position: "relative" }}>
                  <label style={{ position: "absolute", left: 6, top: 6, zIndex: 2, display: "flex", cursor: "pointer", background: "var(--bg)", borderRadius: 4, padding: 3, lineHeight: 0 }} title="选择此道具（用于批量生成）">
                    <input type="checkbox" checked={picked} onChange={() => sel.toggle(prop.id)} disabled={batch.running} />
                  </label>
                  {prop.image_url ? (
                    <img
                      key={prop.image_url}
                      src={mediaDisplayUrl(prop.image_url)}
                      alt={prop.name}
                      onClick={() => setPreview(mediaDisplayUrl(prop.image_url))}
                      style={{ cursor: "zoom-in", width: "100%", height: "100%", objectFit: "cover" }}
                      title="点击放大预览"
                    />
                  ) : (
                    <Image size={30} />
                  )}
                  {generating === prop.id && <span style={{ position: "absolute", right: 6, top: 6, zIndex: 2, color: "var(--green-t)" }}><LoaderCircle className="spin" size={16} /></span>}
                </div>
                <AssetHistoryStrip
                  targetType="prop"
                  targetId={prop.id}
                  currentImageUrl={prop.image_url}
                  onUse={(imageUrl) => setProps((previous) => previous.map((item) => item.id === prop.id ? { ...item, image_url: imageUrl } : item))}
                />
                <div className="asset-content">
                  <strong>{prop.name}</strong>
                  <span>{prop.type || prop.description || "未设置道具类型"}</span>
                  <label style={{ fontSize: 11, color: "var(--text3)" }}>出图提示词（可编辑）</label>
                  <textarea value={prompts[prop.id] ?? ""} onChange={(event) => setPrompts((previous) => ({ ...previous, [prop.id]: event.target.value }))} rows={4} style={{ resize: "vertical", fontSize: 12, lineHeight: 1.5 }} placeholder="描述这个道具的画面提示词…" />
                  <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
                    <button
                      className="btn-secondary"
                      style={{ padding: "4px 10px", fontSize: 12 }}
                      disabled={polishing === prop.id || batch.running}
                      onClick={() => polishPromptForProp(prop)}
                      title="AI 润色提示词"
                    >
                      {polishing === prop.id ? <LoaderCircle className="spin" size={13} /> : <Wand2 size={13} />}
                      AI 润色
                    </button>
                  </div>
                  <AssetGenerationControls
                    variant="prop"
                    resolution={resolutions[prop.id] ?? "square_1024x1024"}
                    steps={stepsById[prop.id] ?? 4}
                    nodeId={nodeIds[prop.id]}
                    nodes={nodes}
                    disabled={batch.running}
                    onResolutionChange={(value) => setResolutions((previous) => ({ ...previous, [prop.id]: value }))}
                    onStepsChange={(value) => setStepsById((previous) => ({ ...previous, [prop.id]: value }))}
                    onNodeChange={(value) => setNodeIds((previous) => ({ ...previous, [prop.id]: value }))}
                  />
                  <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--text3)" }}>
                    道具可调<strong>分辨率 + 采样步数</strong>。出图会<strong>自动删掉提示词里的桌面/战场/房间等环境句</strong>，
                    并强制<strong>浅灰/纯白影棚底板</strong>（与人物定装同类）。若旧提示词含场景，重新生成即可。
                  </p>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <button className="btn-secondary" style={{ flex: "none", padding: "5px 10px" }} disabled={saving != null || batch.running} onClick={() => savePrompt(prop)} title="只保存提示词，不出图">
                      {saving === prop.id ? <LoaderCircle className="spin" size={13} /> : <Save size={13} />} 保存
                    </button>
                    <MediaUploadButton
                      targetType="prop"
                      targetId={prop.id}
                      username={username}
                      accept="image/png,image/jpeg,image/webp,image/gif"
                      label=" 上传图"
                      title="从本机上传道具参考图"
                      disabled={batch.running}
                      style={{ flex: "none", padding: "5px 10px" }}
                      onDone={async (r) => {
                        const raw = r.image_url || r.url;
                        setProps((prev) => prev.map((item) => (item.id === prop.id ? { ...item, image_url: raw } : item)));
                        if (projectId != null) {
                          try {
                            await loadProps(projectId);
                          } catch {
                            /* ignore */
                          }
                        }
                        setNotice("道具图已上传并刷新");
                      }}
                      onError={(m) => setNotice(m)}
                    />
                    <button className="btn-secondary" style={{ flex: 1 }} disabled={generating != null || batch.running} onClick={() => generate(prop)}>
                      {generating === prop.id ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}
                      {prop.image_url ? "重新生成" : "生成道具图"}
                    </button>
                  </div>
                </div>
              </article>
            );
          })}
          {!props.length && <div className="empty-state"><Package size={24} /> 当前项目暂无道具。点右上「AI 提取道具」，从分集剧本自动提取。</div>}
        </div>
      </div>
      <ImageLightbox src={preview} onClose={() => setPreview(null)} />
    </div>
  );
}
