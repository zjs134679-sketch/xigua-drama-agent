import { useEffect, useState } from "react";
import { AlertTriangle, Image, LoaderCircle, Mountain, Save, Sparkles, Wand2 } from "lucide-react";
import {
  extractFromEpisode,
  generateSceneAsset,
  listArtStyles,
  listComputeNodes,
  listProjects,
  listScenes,
  mediaDisplayUrl,
  polishPrompt,
  updateScenePrompt,
  type ArtStyle,
  type AssetResolution,
  type ComputeNodeRecord,
  type Project,
  type SceneAsset,
} from "../api/client";
import BatchBar from "../components/BatchBar";
import AssetGenerationControls from "../components/AssetGenerationControls";
import AssetHistoryStrip from "../components/AssetHistoryStrip";
import AdditionalInstructionField from "../components/AdditionalInstructionField";
import ImageLightbox from "../components/ImageLightbox";
import MediaUploadButton from "../components/MediaUploadButton";
import { notifyAssetsChanged } from "../components/PipelineBar";
import { useBatchRun, type BatchOutcome } from "../components/useBatchRun";
import { useSelection } from "../components/useSelection";
import { followProjectStyleLabel, resolveStyleName } from "./styleHelpers";

export default function SceneAssetsView({
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
  const [scenes, setScenes] = useState<SceneAsset[]>([]);
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
        const preferred = currentDramaId != null && rows.some((r) => r.id === currentDramaId) ? currentDramaId : rows[0].id;
        setProjectId(preferred);
      }
    }).catch((e: Error) => setNotice(e.message));
    listArtStyles().then(setStyles).catch(() => undefined);
    listComputeNodes().then(setNodes).catch(() => undefined);
    setStyleId(undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentDramaId]);

  const activeProject = projects.find((p) => p.id === projectId) ?? null;
  const effectiveStyleLabel = resolveStyleName(styles, styleId, activeProject);

  const loadScenes = (id: number) =>
    listScenes(id)
      .then((rows) => {
        setScenes(rows);
        notifyAssetsChanged();
        setPrompts((prev) => {
          const next = { ...prev };
          rows.forEach((s) => {
            if (next[s.id] === undefined) next[s.id] = s.prompt ?? "";
          });
          return next;
        });
      })
      .catch((e: Error) => setNotice(e.message));
  useEffect(() => {
    if (projectId != null) loadScenes(projectId);
    sel.clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const savePrompt = async (scene: SceneAsset) => {
    setSaving(scene.id);
    setNotice("");
    try {
      await updateScenePrompt(scene.id, prompts[scene.id] ?? "");
      setNotice(`已保存「${scene.location ?? "场景"}」的提示词`);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(null);
    }
  };

  const polishPromptForScene = async (scene: SceneAsset) => {
    setPolishing(scene.id);
    setNotice("");
    try {
      const result = await polishPrompt({
        asset_type: "scene",
        prompt: prompts[scene.id] ?? "",
        context: [scene.location, scene.time].filter(Boolean).join("，"),
      });
      setPrompts((previous) => ({ ...previous, [scene.id]: result.polished }));
      setNotice(`已润色「${scene.location ?? "场景"}」的提示词`);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "润色失败");
    } finally {
      setPolishing(null);
    }
  };

  const generateCore = async (scene: SceneAsset): Promise<BatchOutcome> => {
    setGenerating(scene.id);
    try {
      const result = await generateSceneAsset({
        scene_id: scene.id,
        prompt: prompts[scene.id] ?? undefined,
        art_style_id: styleId,
        username,
        node_id: nodeIds[scene.id],
        resolution: resolutions[scene.id] ?? "uhd_landscape_1280x720",
        steps: stepsById[scene.id] ?? 32,
        extra: extra.trim() || undefined,
      });
      return { ok: !result.blocked, message: result.blocked ? result.message ?? "提示词触发红线" : undefined };
    } catch (e) {
      const msg = e instanceof Error ? e.message : "生成失败";
      return { ok: false, message: msg, banned: msg.includes("封") };
    } finally {
      setGenerating(null);
    }
  };

  const generate = async (scene: SceneAsset) => {
    setNotice("");
    const outcome = await generateCore(scene);
    setNotice(outcome.ok ? "场景图生成完成。" : outcome.message ?? "生成失败");
    if (projectId != null) await loadScenes(projectId);
  };

  const runBatch = async () => {
    const queue = scenes.filter((s) => sel.selected.has(s.id));
    if (!queue.length) return;
    setNotice("");
    const byId = new Map(scenes.map((s) => [s.id, s]));
    const res = await batch.run(queue.map((s) => s.id), (id) => generateCore(byId.get(id)!));
    if (projectId != null) await loadScenes(projectId);
    if (!res) return;
    const ok = res.done - res.failed;
    if (res.banned) setNotice("账号已被封禁，已停止批量生成。");
    else if (res.stopped) setNotice(`已停止：成功 ${ok}/${res.total}，失败 ${res.failed}。`);
    else setNotice(`批量完成：成功 ${ok}、失败 ${res.failed}，共 ${res.total}。${res.lastMessage ? `（最后一条：${res.lastMessage}）` : ""}`);
  };

  const extract = async () => {
    if (currentEpisodeId == null) {
      setNotice("请先在「项目」里进入一个分集，再从它的剧本提取场景。");
      return;
    }
    setExtracting(true);
    setNotice("");
    try {
      const res = await extractFromEpisode(currentEpisodeId, username);
      if (res.status === 200 && res.new) {
        setNotice(`提取完成：场景 +${res.new.scenes}、角色 +${res.new.characters}、道具 +${res.new.props}`);
        if (projectId != null) await loadScenes(projectId);
      } else {
        setNotice(res.message || "提取失败");
      }
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "提取失败");
    } finally {
      setExtracting(false);
    }
  };

  const allIds = scenes.map((s) => s.id);
  const ungeneratedIds = scenes.filter((s) => !s.image_url).map((s) => s.id);
  const progressText = batch.progress
    ? `生成中 ${batch.progress.done}/${batch.progress.total}${batch.progress.failed ? `（失败 ${batch.progress.failed}）` : ""}`
    : null;

  return (
    <div className="feature-view">
      <div className="feature-header">
        <div>
          <h2>场景 / 造景</h2>
          <p>
            从剧本提取场景，生成<strong>纯环境空镜</strong>（无人）参考图 · 当前出图画风：
            <strong style={{ color: "var(--green-t)" }}>{effectiveStyleLabel}</strong>
          </p>
        </div>
        <div className="feature-filters">
          <button className="btn-primary" style={{ width: "auto", padding: "6px 14px" }} disabled={extracting} onClick={extract} title="从当前分集的剧本/原文中提取角色、场景、道具">
            {extracting ? <LoaderCircle className="spin" size={14} /> : <Wand2 size={14} />} AI 提取角色/场景
          </button>
          <select
            value={projectId ?? ""}
            onChange={(e) => {
              setProjectId(Number(e.target.value));
              setStyleId(undefined);
            }}
          >
            {!projects.length && <option value="">暂无项目</option>}
            {projects.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
          </select>
          <select
            value={styleId ?? ""}
            onChange={(e) => setStyleId(e.target.value ? Number(e.target.value) : undefined)}
            title="默认跟随项目画风"
          >
            <option value="">{followProjectStyleLabel(activeProject)}</option>
            {styles.map((style) => <option key={style.id} value={style.id}>{style.name}（仅本次出图）</option>)}
          </select>
        </div>
      </div>
      <AdditionalInstructionField value={extra} onChange={setExtra} disabled={batch.running} />
      <BatchBar
        total={scenes.length}
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
        runLabel="批量生成场景图"
      />
      {notice && <div className="feature-notice"><AlertTriangle size={14} /> {notice}</div>}
      <div className="feature-body">
        <div className="asset-grid character-grid">
          {scenes.map((scene) => {
            const picked = sel.selected.has(scene.id);
            return (
            <article className="asset-card" key={scene.id} style={picked ? { outline: "2px solid var(--green)", outlineOffset: -1 } : undefined}>
              <div className="asset-preview" style={{ position: "relative" }}>
                <label
                  style={{ position: "absolute", left: 6, top: 6, zIndex: 2, display: "flex", cursor: "pointer", background: "var(--bg)", borderRadius: 4, padding: 3, lineHeight: 0 }}
                  title="选择此场景（用于批量生成）"
                >
                  <input type="checkbox" checked={picked} onChange={() => sel.toggle(scene.id)} disabled={batch.running} />
                </label>
                {scene.image_url ? (
                  <img
                    key={scene.image_url}
                    src={mediaDisplayUrl(scene.image_url)}
                    alt={scene.location ?? ""}
                    onClick={() => setPreview(mediaDisplayUrl(scene.image_url))}
                    style={{ cursor: "zoom-in", width: "100%", height: "100%", objectFit: "cover" }}
                    title="点击放大预览"
                  />
                ) : (
                  <Image size={30} />
                )}
                {generating === scene.id && (
                  <span style={{ position: "absolute", right: 6, top: 6, zIndex: 2, color: "var(--green-t)" }}><LoaderCircle className="spin" size={16} /></span>
                )}
              </div>
              <AssetHistoryStrip
                targetType="scene"
                targetId={scene.id}
                currentImageUrl={scene.image_url}
                onUse={(imageUrl) => setScenes((previous) => previous.map((item) => item.id === scene.id ? { ...item, image_url: imageUrl } : item))}
              />
              <div className="asset-content">
                <strong>{scene.location || "未命名场景"}</strong>
                <span>{scene.time || "未设置时间"}</span>
                <label style={{ fontSize: 11, color: "var(--text3)" }}>背景提示词（可编辑）</label>
                <textarea
                  value={prompts[scene.id] ?? ""}
                  onChange={(e) => setPrompts((p) => ({ ...p, [scene.id]: e.target.value }))}
                  rows={4}
                  style={{ resize: "vertical", fontSize: 12, lineHeight: 1.5 }}
                  placeholder="描述这个场景的纯背景画面提示词…"
                />
                <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
                  <button
                    className="btn-secondary"
                    style={{ padding: "4px 10px", fontSize: 12 }}
                    disabled={polishing === scene.id || batch.running}
                    onClick={() => polishPromptForScene(scene)}
                    title="AI 润色提示词"
                  >
                    {polishing === scene.id ? <LoaderCircle className="spin" size={13} /> : <Wand2 size={13} />}
                    AI 润色
                  </button>
                </div>
                <AssetGenerationControls
                  variant="scene"
                  resolution={resolutions[scene.id] ?? "uhd_landscape_1280x720"}
                  steps={stepsById[scene.id] ?? 4}
                  nodeId={nodeIds[scene.id]}
                  nodes={nodes}
                  disabled={batch.running}
                  onResolutionChange={(value) => setResolutions((previous) => ({ ...previous, [scene.id]: value }))}
                  onStepsChange={(value) => setStepsById((previous) => ({ ...previous, [scene.id]: value }))}
                  onNodeChange={(value) => setNodeIds((previous) => ({ ...previous, [scene.id]: value }))}
                />
                <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--text3)" }}>
                  场景与角色一样可调<strong>分辨率 + 采样步数</strong>；默认超清横屏 1280×720 · 32 步。糊了就提高步数后「重新生成」。
                </p>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <button className="btn-secondary" style={{ flex: "none", padding: "5px 10px" }} disabled={saving != null || batch.running} onClick={() => savePrompt(scene)} title="只保存提示词，不出图">
                    {saving === scene.id ? <LoaderCircle className="spin" size={13} /> : <Save size={13} />} 保存
                  </button>
                  <MediaUploadButton
                    targetType="scene"
                    targetId={scene.id}
                    username={username}
                    accept="image/png,image/jpeg,image/webp,image/gif"
                    label=" 上传图"
                    title="从本机上传场景参考图"
                    disabled={batch.running}
                    style={{ flex: "none", padding: "5px 10px" }}
                    onDone={async (r) => {
                      const raw = r.image_url || r.url;
                      setScenes((prev) => prev.map((item) => (item.id === scene.id ? { ...item, image_url: raw } : item)));
                      if (projectId != null) {
                        try {
                          await loadScenes(projectId);
                        } catch {
                          /* ignore */
                        }
                      }
                      setNotice("场景图已上传并刷新");
                    }}
                    onError={(m) => setNotice(m)}
                  />
                  <button className="btn-secondary" style={{ flex: 1 }} disabled={generating != null || batch.running} onClick={() => generate(scene)}>
                    {generating === scene.id ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}
                    {scene.image_url ? "重新生成" : "生成场景图"}
                  </button>
                </div>
              </div>
            </article>
            );
          })}
          {!scenes.length && <div className="empty-state"><Mountain size={24} /> 当前项目暂无场景。点右上「AI 提取角色/场景」，从分集剧本自动提取。</div>}
        </div>
      </div>
      <ImageLightbox src={preview} onClose={() => setPreview(null)} />
    </div>
  );
}
