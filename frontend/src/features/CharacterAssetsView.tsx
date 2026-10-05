import { useEffect, useState } from "react";
import { AlertTriangle, Image, LoaderCircle, Save, Sparkles, Users, Wand2 } from "lucide-react";
import {
  extractFromEpisode,
  generateCharacterAsset,
  listArtStyles,
  listCharacters,
  listComputeNodes,
  listProjects,
  mediaDisplayUrl,
  polishPrompt,
  updateCharacterPrompt,
  type ArtStyle,
  type AssetResolution,
  type CharacterAsset,
  type ComputeNodeRecord,
  type Project,
} from "../api/client";
import BatchBar from "../components/BatchBar";
import AssetGenerationControls from "../components/AssetGenerationControls";
import AssetHistoryStrip from "../components/AssetHistoryStrip";
import ImageLightbox from "../components/ImageLightbox";
import AdditionalInstructionField from "../components/AdditionalInstructionField";
import MediaUploadButton from "../components/MediaUploadButton";
import { notifyAssetsChanged } from "../components/PipelineBar";
import { useBatchRun, type BatchOutcome } from "../components/useBatchRun";
import { useSelection } from "../components/useSelection";
import { followProjectStyleLabel, resolveStyleName } from "./styleHelpers";

export default function CharacterAssetsView({
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
  const [characters, setCharacters] = useState<CharacterAsset[]>([]);
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
  const [viewTypes, setViewTypes] = useState<Record<number, string>>({});
  const [notice, setNotice] = useState("");
  // Session-only page instruction; it is sent per generation and never saved with the asset prompt.
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
    // 换项目时恢复「跟随项目画风」，避免沿用上一次临时覆盖
    setStyleId(undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentDramaId]);

  const activeProject = projects.find((p) => p.id === projectId) ?? null;
  const effectiveStyleLabel = resolveStyleName(styles, styleId, activeProject);

  const loadCharacters = (id: number) =>
    listCharacters(id)
      .then((rows) => {
        setCharacters(rows);
        notifyAssetsChanged();
        setPrompts((prev) => {
          const next = { ...prev };
          rows.forEach((c) => {
            if (next[c.id] === undefined) next[c.id] = c.image_prompt ?? "";
          });
          return next;
        });
        setViewTypes((prev) => {
          const next = { ...prev };
          rows.forEach((c) => {
            if (next[c.id] === undefined) next[c.id] = c.view_type ?? "turnaround_head";
          });
          return next;
        });
      })
      .catch((e: Error) => setNotice(e.message));
  useEffect(() => {
    if (projectId != null) loadCharacters(projectId);
    sel.clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const savePrompt = async (character: CharacterAsset) => {
    setSaving(character.id);
    setNotice("");
    try {
      await updateCharacterPrompt(character.id, prompts[character.id] ?? "");
      setNotice(`已保存「${character.name}」的提示词`);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(null);
    }
  };

  const polishPromptForCharacter = async (character: CharacterAsset) => {
    setPolishing(character.id);
    setNotice("");
    try {
      const result = await polishPrompt({
        asset_type: "character",
        prompt: prompts[character.id] ?? "",
        context: [character.name, character.role, character.appearance, character.personality].filter(Boolean).join("，"),
      });
      setPrompts((previous) => ({ ...previous, [character.id]: result.polished }));
      setNotice(`已润色「${character.name}」的提示词`);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "润色失败");
    } finally {
      setPolishing(null);
    }
  };

  // 单项出图核心：用编辑后的提示词，返回结果但不弹通知/不刷新（批量由调用方统一处理）。
  const generateCore = async (character: CharacterAsset): Promise<BatchOutcome> => {
    setGenerating(character.id);
    try {
      const vt = viewTypes[character.id] ?? "turnaround_head";
      // 三视图默认横屏；头像/单人全身默认竖屏（可在卡片上改）
      const defaultRes =
        vt === "headshot"
          ? "uhd_portrait_1152x1536"
          : vt === "turnaround" || vt === "turnaround_head"
            ? "uhd_landscape_1280x720"
            : "uhd_portrait_1024x1344";
      const result = await generateCharacterAsset({
        character_id: character.id,
        prompt: prompts[character.id] ?? undefined,
        art_style_id: styleId,
        username,
        node_id: nodeIds[character.id],
        resolution: resolutions[character.id] ?? defaultRes,
        steps: stepsById[character.id] ?? 4,
        extra: extra.trim() || undefined,
        view_type: vt,
      });
      return { ok: !result.blocked, message: result.blocked ? result.message ?? "提示词触发红线" : undefined };
    } catch (e) {
      const msg = e instanceof Error ? e.message : "生成失败";
      return { ok: false, message: msg, banned: msg.includes("封") };
    } finally {
      setGenerating(null);
    }
  };

  const generate = async (character: CharacterAsset) => {
    setNotice("");
    const outcome = await generateCore(character);
    setNotice(outcome.ok ? "素材生成完成。" : outcome.message ?? "生成失败");
    if (projectId != null) await loadCharacters(projectId);
  };

  const runBatch = async () => {
    const queue = characters.filter((c) => sel.selected.has(c.id));
    if (!queue.length) return;
    setNotice("");
    const byId = new Map(characters.map((c) => [c.id, c]));
    const res = await batch.run(queue.map((c) => c.id), (id) => generateCore(byId.get(id)!));
    if (projectId != null) await loadCharacters(projectId);
    if (!res) return;
    const ok = res.done - res.failed;
    if (res.banned) setNotice("账号已被封禁，已停止批量生成。");
    else if (res.stopped) setNotice(`已停止：成功 ${ok}/${res.total}，失败 ${res.failed}。`);
    else setNotice(`批量完成：成功 ${ok}、失败 ${res.failed}，共 ${res.total}。${res.lastMessage ? `（最后一条：${res.lastMessage}）` : ""}`);
  };

  const extract = async () => {
    if (currentEpisodeId == null) {
      setNotice("请先在「项目」里进入一个分集，再从它的剧本提取角色。");
      return;
    }
    setExtracting(true);
    setNotice("");
    try {
      const res = await extractFromEpisode(currentEpisodeId, username);
      if (res.status === 200 && res.new) {
        setNotice(`提取完成：角色 +${res.new.characters}、场景 +${res.new.scenes}、道具 +${res.new.props}`);
        if (projectId != null) await loadCharacters(projectId);
      } else {
        setNotice(res.message || "提取失败");
      }
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "提取失败");
    } finally {
      setExtracting(false);
    }
  };

  const allIds = characters.map((c) => c.id);
  const ungeneratedIds = characters.filter((c) => !c.image_url).map((c) => c.id);
  const progressText = batch.progress
    ? `生成中 ${batch.progress.done}/${batch.progress.total}${batch.progress.failed ? `（失败 ${batch.progress.failed}）` : ""}`
    : null;

  return (
    <div className="feature-view">
      <div className="feature-header">
        <div>
          <h2>角色资产</h2>
          <p>从角色设定生成可复用的一致性参考图 · 当前出图画风：<strong style={{ color: "var(--green-t)" }}>{effectiveStyleLabel}</strong></p>
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
            title="默认跟随项目画风；仅当需要临时换风时再选手动项"
          >
            <option value="">{followProjectStyleLabel(activeProject)}</option>
            {styles.map((style) => <option key={style.id} value={style.id}>{style.name}（仅本次出图）</option>)}
          </select>
        </div>
      </div>
      <AdditionalInstructionField value={extra} onChange={setExtra} disabled={batch.running} />
      <BatchBar
        total={characters.length}
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
        runLabel="批量生成角色图"
      />
      {notice && <div className="feature-notice"><AlertTriangle size={14} /> {notice}</div>}
      <div className="feature-body">
        <div className="asset-grid character-grid">
          {characters.map((character) => {
            const picked = sel.selected.has(character.id);
            return (
            <article className="asset-card" key={character.id} style={picked ? { outline: "2px solid var(--green)", outlineOffset: -1 } : undefined}>
              <div className="asset-preview portrait" style={{ position: "relative" }}>
                <label
                  style={{ position: "absolute", left: 6, top: 6, zIndex: 2, display: "flex", cursor: "pointer", background: "var(--bg)", borderRadius: 4, padding: 3, lineHeight: 0 }}
                  title="选择此角色（用于批量生成）"
                >
                  <input type="checkbox" checked={picked} onChange={() => sel.toggle(character.id)} disabled={batch.running} />
                </label>
                {character.image_url ? (
                  <img
                    key={character.image_url}
                    src={mediaDisplayUrl(character.image_url)}
                    alt={character.name}
                    onClick={() => setPreview(mediaDisplayUrl(character.image_url))}
                    style={{ cursor: "zoom-in", width: "100%", height: "100%", objectFit: "cover" }}
                    title="点击放大预览"
                  />
                ) : (
                  <Image size={30} />
                )}
                {generating === character.id && (
                  <span style={{ position: "absolute", right: 6, top: 6, zIndex: 2, color: "var(--green-t)" }}><LoaderCircle className="spin" size={16} /></span>
                )}
              </div>
              <AssetHistoryStrip
                targetType="character"
                targetId={character.id}
                currentImageUrl={character.image_url}
                onUse={(imageUrl) => setCharacters((previous) => previous.map((item) => item.id === character.id ? { ...item, image_url: imageUrl } : item))}
              />
              <div className="asset-content">
                <strong>{character.name}</strong>
                <span>{character.role || "未设置角色类型"}</span>
                <label style={{ fontSize: 11, color: "var(--text3)" }}>出图提示词（可编辑）</label>
                <textarea
                  value={prompts[character.id] ?? ""}
                  onChange={(e) => setPrompts((p) => ({ ...p, [character.id]: e.target.value }))}
                  rows={4}
                  style={{ resize: "vertical", fontSize: 12, lineHeight: 1.5 }}
                  placeholder="描述这个角色的画面提示词…"
                />
                <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
                  <button
                    className="btn-secondary"
                    style={{ padding: "4px 10px", fontSize: 12 }}
                    disabled={polishing === character.id || batch.running}
                    onClick={() => polishPromptForCharacter(character)}
                    title="AI 润色提示词"
                  >
                    {polishing === character.id ? <LoaderCircle className="spin" size={13} /> : <Wand2 size={13} />}
                    AI 润色
                  </button>
                </div>
                <label style={{ fontSize: 11, color: "var(--text3)" }}>出图视角（版式）</label>
                <select
                  value={viewTypes[character.id] ?? "turnaround_head"}
                  disabled={batch.running}
                  onChange={(e) => {
                    const vt = e.target.value;
                    setViewTypes((prev) => ({ ...prev, [character.id]: vt }));
                    // 三视图/带头特写拼图默认横屏；头像与单人全身默认竖屏
                    if (vt === "headshot" || vt === "turnaround_head" || vt === "turnaround" || vt === "full_body" || vt === "side") {
                      const nextRes =
                        vt === "headshot"
                          ? "uhd_portrait_1152x1536"
                          : vt === "turnaround" || vt === "turnaround_head"
                            ? "uhd_landscape_1280x720"
                            : "uhd_portrait_1024x1344";
                      setResolutions((prev) => ({
                        ...prev,
                        [character.id]: nextRes,
                      }));
                    }
                  }}
                  style={{ marginBottom: 4 }}
                >
                  <option value="turnaround_head">三视图 + 头部特写（推荐·定装）</option>
                  <option value="turnaround">仅三视图（正/侧/背全身）</option>
                  <option value="headshot">仅头部特写（脸部锁定）</option>
                  <option value="full_body">全身正面（单人）</option>
                  <option value="side">侧面全身（单人）</option>
                </select>
                <p style={{ margin: "0 0 8px", fontSize: 11, color: "var(--text3)", lineHeight: 1.45 }}>
                  {(viewTypes[character.id] ?? "turnaround_head") === "turnaround_head" &&
                    "一张图含：正面/侧面/背面全身 + 放大脸部特写，后续分镜认脸更稳。"}
                  {(viewTypes[character.id] ?? "turnaround_head") === "turnaround" &&
                    "仅 3 个全身横排，不含单独大头特写。"}
                  {(viewTypes[character.id] ?? "turnaround_head") === "headshot" &&
                    "只出肩部以上大特写，脸部占满画面，适合做人脸参考。"}
                  {(viewTypes[character.id] ?? "turnaround_head") === "full_body" &&
                    "单人全身正面一张。"}
                  {(viewTypes[character.id] ?? "turnaround_head") === "side" &&
                    "单人纯侧面全身一张。"}
                </p>
                <AssetGenerationControls
                  variant="character"
                  resolution={
                    resolutions[character.id] ??
                    ((viewTypes[character.id] ?? "turnaround_head") === "turnaround" ||
                    (viewTypes[character.id] ?? "turnaround_head") === "turnaround_head"
                      ? "uhd_landscape_1280x720"
                      : "uhd_portrait_1024x1344")
                  }
                  steps={stepsById[character.id] ?? 4}
                  nodeId={nodeIds[character.id]}
                  nodes={nodes}
                  disabled={batch.running}
                  onResolutionChange={(value) => setResolutions((previous) => ({ ...previous, [character.id]: value }))}
                  onStepsChange={(value) => setStepsById((previous) => ({ ...previous, [character.id]: value }))}
                  onNodeChange={(value) => setNodeIds((previous) => ({ ...previous, [character.id]: value }))}
                />
                <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--text3)" }}>
                  上面只调<strong>角色定妆图</strong>：三视图默认<strong>横屏</strong>，头像/全身默认<strong>竖屏</strong>，分辨率下拉可改。
                  当前为 H3 Turbo 4 步：步数请保持 4（提再高会与 Turbo LoRA 冲突）。更清晰优先提高分辨率（竖图 768×1344 内）。视频尺寸在「成片」台另设。
                </p>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <button className="btn-secondary" style={{ flex: "none", padding: "5px 10px" }} disabled={saving != null || batch.running} onClick={() => savePrompt(character)} title="只保存提示词，不出图">
                    {saving === character.id ? <LoaderCircle className="spin" size={13} /> : <Save size={13} />} 保存
                  </button>
                  <MediaUploadButton
                    targetType="character"
                    targetId={character.id}
                    username={username}
                    accept="image/png,image/jpeg,image/webp,image/gif"
                    label=" 上传图"
                    title="从本机上传角色参考图"
                    disabled={batch.running}
                    style={{ flex: "none", padding: "5px 10px" }}
                    onDone={async (r) => {
                      // 库路径（/oss/...），展示层再转 /api + 缓存破坏
                      const raw = r.image_url || r.url;
                      setCharacters((prev) =>
                        prev.map((item) =>
                          item.id === character.id ? { ...item, image_url: raw } : item,
                        ),
                      );
                      if (projectId != null) {
                        try {
                          await loadCharacters(projectId);
                        } catch {
                          /* ignore */
                        }
                      }
                      setNotice("角色图已上传并刷新");
                    }}
                    onError={(m) => setNotice(m)}
                  />
                  <button className="btn-secondary" style={{ flex: 1 }} disabled={generating != null || batch.running} onClick={() => generate(character)}>
                    {generating === character.id ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}
                    {character.image_url ? "重新生成" : "生成角色图"}
                  </button>
                </div>
              </div>
            </article>
            );
          })}
          {!characters.length && <div className="empty-state"><Users size={24} /> 当前项目暂无角色。点右上「AI 提取角色/场景」，从分集剧本自动提取。</div>}
        </div>
      </div>
      <ImageLightbox src={preview} onClose={() => setPreview(null)} />
    </div>
  );
}
