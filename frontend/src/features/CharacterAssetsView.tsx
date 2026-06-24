import { useEffect, useRef, useState } from "react";
import { AlertTriangle, AudioLines, Image, LoaderCircle, Save, Sparkles, Users, Volume2, Wand2 } from "lucide-react";
import {
  assignVoices,
  extractFromEpisode,
  generateCharacterAsset,
  listArtStyles,
  listCharacters,
  listComputeNodes,
  listProjects,
  listVoices,
  polishPrompt,
  previewVoice,
  updateCharacterPrompt,
  updateCharacterVoice,
  type ArtStyle,
  type AssetResolution,
  type CharacterAsset,
  type ComputeNodeRecord,
  type Project,
  type VoiceRecord,
} from "../api/client";
import BatchBar from "../components/BatchBar";
import AssetGenerationControls from "../components/AssetGenerationControls";
import AssetHistoryStrip from "../components/AssetHistoryStrip";
import ImageLightbox from "../components/ImageLightbox";
import AdditionalInstructionField from "../components/AdditionalInstructionField";
import { useBatchRun, type BatchOutcome } from "../components/useBatchRun";
import { useSelection } from "../components/useSelection";

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
  const [nodeIds, setNodeIds] = useState<Record<number, number | undefined>>({});
  const [voices, setVoices] = useState<VoiceRecord[]>([]);
  const [assigningVoices, setAssigningVoices] = useState(false);
  const [bindingVoice, setBindingVoice] = useState<number | null>(null);
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
  const [previewingVoice, setPreviewingVoice] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const sel = useSelection();
  const batch = useBatchRun();

  const auditionVoice = async (voiceId: string | null | undefined) => {
    if (!voiceId) { setNotice("请先给这个角色选一个音色，再试听。"); return; }
    setPreviewingVoice(voiceId);
    try {
      const { audio_url } = await previewVoice(voiceId);
      if (!audioRef.current) audioRef.current = new Audio();
      audioRef.current.src = audio_url;
      await audioRef.current.play();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "试听失败");
    } finally {
      setPreviewingVoice(null);
    }
  };

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
    listVoices().then(setVoices).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentDramaId]);

  const loadCharacters = (id: number) =>
    listCharacters(id)
      .then((rows) => {
        setCharacters(rows);
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
            if (next[c.id] === undefined) next[c.id] = c.view_type ?? "turnaround";
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
      const result = await generateCharacterAsset({
        character_id: character.id,
        prompt: prompts[character.id] ?? undefined,
        art_style_id: styleId,
        username,
        node_id: nodeIds[character.id],
        resolution: resolutions[character.id] ?? "portrait_768x1024",
        extra: extra.trim() || undefined,
        view_type: viewTypes[character.id] ?? "turnaround",
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

  const assignAllVoices = async () => {
    if (projectId == null) return;
    setAssigningVoices(true);
    setNotice("");
    try {
      const result = await assignVoices(projectId);
      setNotice(result.assignments.length ? `已为 ${result.assignments.length} 个角色绑定音色。` : "所有角色都已绑定音色。");
      await loadCharacters(projectId);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "音色绑定失败");
    } finally {
      setAssigningVoices(false);
    }
  };

  const bindVoice = async (character: CharacterAsset, voiceId: string) => {
    const voice = voices.find((item) => item.voice_id === voiceId);
    if (!voice) return;
    setBindingVoice(character.id);
    setNotice("");
    try {
      await updateCharacterVoice(character.id, voice.voice_id, voice.provider);
      setCharacters((previous) => previous.map((item) => item.id === character.id
        ? { ...item, voice_id: voice.voice_id, voice_provider: voice.provider }
        : item));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "音色绑定失败");
    } finally {
      setBindingVoice(null);
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
        <div><h2>角色资产</h2><p>从角色设定生成可复用的一致性参考图</p></div>
        <div className="feature-filters">
          <button className="btn-secondary" style={{ width: "auto", padding: "6px 12px" }} disabled={assigningVoices || projectId == null} onClick={assignAllVoices} title="为尚未绑定音色的角色自动选择音色元数据">
            {assigningVoices ? <LoaderCircle className="spin" size={14} /> : <AudioLines size={14} />} 一键绑定音频
          </button>
          <button className="btn-primary" style={{ width: "auto", padding: "6px 14px" }} disabled={extracting} onClick={extract} title="从当前分集的剧本/原文中提取角色、场景、道具">
            {extracting ? <LoaderCircle className="spin" size={14} /> : <Wand2 size={14} />} AI 提取角色/场景
          </button>
          <select value={projectId ?? ""} onChange={(e) => setProjectId(Number(e.target.value))}>
            {!projects.length && <option value="">暂无项目</option>}
            {projects.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
          </select>
          <select value={styleId ?? ""} onChange={(e) => setStyleId(e.target.value ? Number(e.target.value) : undefined)}>
            <option value="">默认画风</option>
            {styles.map((style) => <option key={style.id} value={style.id}>{style.name}</option>)}
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
                {character.image_url ? <img src={character.image_url} alt={character.name} onClick={() => setPreview(character.image_url)} style={{ cursor: "zoom-in" }} title="点击放大预览" /> : <Image size={30} />}
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
                <label style={{ fontSize: 11, color: "var(--text3)" }}>出图视角</label>
                <select
                  value={viewTypes[character.id] ?? "turnaround"}
                  disabled={batch.running}
                  onChange={(e) => setViewTypes((prev) => ({ ...prev, [character.id]: e.target.value }))}
                  style={{ marginBottom: 6 }}
                >
                  <option value="turnaround">角色四视图设定图</option>
                  <option value="full_body">全身正面</option>
                  <option value="headshot">头像特写</option>
                  <option value="side">侧面</option>
                </select>
                <AssetGenerationControls
                  resolution={resolutions[character.id] ?? "portrait_768x1024"}
                  nodeId={nodeIds[character.id]}
                  nodes={nodes}
                  disabled={batch.running}
                  onResolutionChange={(value) => setResolutions((previous) => ({ ...previous, [character.id]: value }))}
                  onNodeChange={(value) => setNodeIds((previous) => ({ ...previous, [character.id]: value }))}
                />
                <label style={{ fontSize: 11, color: "var(--text3)" }}>绑定音色（可自选 + 试听）</label>
                <div style={{ display: "flex", gap: 6 }}>
                  <select
                    style={{ flex: 1, minWidth: 0 }}
                    value={character.voice_id ?? ""}
                    disabled={bindingVoice === character.id || batch.running}
                    onChange={(event) => bindVoice(character, event.target.value)}
                  >
                    <option value="">未绑定</option>
                    {voices.map((voice) => <option key={`${voice.provider}:${voice.voice_id}`} value={voice.voice_id}>{voice.voice_name}</option>)}
                  </select>
                  <button
                    className="btn-secondary"
                    style={{ flex: "none", padding: "5px 9px" }}
                    disabled={!character.voice_id || previewingVoice != null || batch.running}
                    onClick={() => auditionVoice(character.voice_id)}
                    title="试听当前音色"
                  >
                    {previewingVoice === character.voice_id ? <LoaderCircle className="spin" size={13} /> : <Volume2 size={13} />} 试听
                  </button>
                </div>
                <div style={{ display: "flex", gap: 6 }}>
                  <button className="btn-secondary" style={{ flex: "none", padding: "5px 10px" }} disabled={saving != null || batch.running} onClick={() => savePrompt(character)} title="只保存提示词，不出图">
                    {saving === character.id ? <LoaderCircle className="spin" size={13} /> : <Save size={13} />} 保存
                  </button>
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
