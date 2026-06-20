import { useEffect, useState } from "react";
import { AlertTriangle, Image, LoaderCircle, Save, Sparkles, Users, Wand2 } from "lucide-react";
import {
  extractFromEpisode,
  generateCharacterAsset,
  listArtStyles,
  listCharacters,
  listProjects,
  updateCharacterPrompt,
  type ArtStyle,
  type CharacterAsset,
  type Project,
} from "../api/client";

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
  const [generating, setGenerating] = useState<number | null>(null);
  const [extracting, setExtracting] = useState(false);
  const [saving, setSaving] = useState<number | null>(null);
  const [prompts, setPrompts] = useState<Record<number, string>>({});
  const [notice, setNotice] = useState("");

  useEffect(() => {
    listProjects().then((rows) => {
      setProjects(rows);
      if (rows.length) {
        const preferred = currentDramaId != null && rows.some((r) => r.id === currentDramaId) ? currentDramaId : rows[0].id;
        setProjectId(preferred);
      }
    }).catch((e: Error) => setNotice(e.message));
    listArtStyles().then(setStyles).catch(() => undefined);
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
      })
      .catch((e: Error) => setNotice(e.message));
  useEffect(() => {
    if (projectId != null) loadCharacters(projectId);
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

  const generate = async (character: CharacterAsset) => {
    setGenerating(character.id);
    setNotice("");
    try {
      const result = await generateCharacterAsset({
        character_id: character.id,
        prompt: prompts[character.id] ?? undefined, // 用编辑后的提示词出图
        art_style_id: styleId,
        username,
      });
      if (result.blocked) {
        setNotice(`红线拦截：${result.message ?? "提示词触发红线"}`);
      } else {
        setNotice(result.warn ? "素材已生成，提示词包含需关注内容。" : "素材生成完成。");
      }
      if (projectId != null) await loadCharacters(projectId);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "生成失败");
    } finally {
      setGenerating(null);
    }
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

  return (
    <div className="feature-view">
      <div className="feature-header">
        <div><h2>角色资产</h2><p>从角色设定生成可复用的一致性参考图</p></div>
        <div className="feature-filters">
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
      {notice && <div className="feature-notice"><AlertTriangle size={14} /> {notice}</div>}
      <div className="feature-body">
        <div className="asset-grid character-grid">
          {characters.map((character) => (
            <article className="asset-card" key={character.id}>
              <div className="asset-preview portrait">
                {character.image_url ? <img src={character.image_url} alt={character.name} /> : <Image size={30} />}
              </div>
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
                <div style={{ display: "flex", gap: 6 }}>
                  <button className="btn-secondary" style={{ flex: "none", padding: "5px 10px" }} disabled={saving != null} onClick={() => savePrompt(character)} title="只保存提示词，不出图">
                    {saving === character.id ? <LoaderCircle className="spin" size={13} /> : <Save size={13} />} 保存
                  </button>
                  <button className="btn-secondary" style={{ flex: 1 }} disabled={generating != null} onClick={() => generate(character)}>
                    {generating === character.id ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}
                    {character.image_url ? "重新生成" : "生成角色图"}
                  </button>
                </div>
              </div>
            </article>
          ))}
          {!characters.length && <div className="empty-state"><Users size={24} /> 当前项目暂无角色。点右上「AI 提取角色/场景」，从分集剧本自动提取。</div>}
        </div>
      </div>
    </div>
  );
}
