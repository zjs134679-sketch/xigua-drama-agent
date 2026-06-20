import { useEffect, useState } from "react";
import { AlertTriangle, Image, LoaderCircle, Sparkles, Users } from "lucide-react";
import {
  generateCharacterAsset,
  listArtStyles,
  listCharacters,
  listProjects,
  type ArtStyle,
  type CharacterAsset,
  type Project,
} from "../api/client";

export default function CharacterAssetsView() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [characters, setCharacters] = useState<CharacterAsset[]>([]);
  const [styles, setStyles] = useState<ArtStyle[]>([]);
  const [styleId, setStyleId] = useState<number | undefined>();
  const [generating, setGenerating] = useState<number | null>(null);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    listProjects().then((rows) => {
      setProjects(rows);
      if (rows.length) setProjectId(rows[0].id);
    }).catch((e: Error) => setNotice(e.message));
    listArtStyles().then(setStyles).catch(() => undefined);
  }, []);

  const loadCharacters = (id: number) => listCharacters(id).then(setCharacters).catch((e: Error) => setNotice(e.message));
  useEffect(() => {
    if (projectId != null) loadCharacters(projectId);
  }, [projectId]);

  const generate = async (character: CharacterAsset) => {
    setGenerating(character.id);
    setNotice("");
    try {
      const result = await generateCharacterAsset({ character_id: character.id, art_style_id: styleId });
      setNotice(result.warn ? "素材已生成，提示词包含需关注内容。" : "素材生成完成。 ");
      if (projectId != null) await loadCharacters(projectId);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "生成失败");
    } finally {
      setGenerating(null);
    }
  };

  return (
    <div className="feature-view">
      <div className="feature-header">
        <div><h2>角色资产</h2><p>从角色设定生成可复用的一致性参考图</p></div>
        <div className="feature-filters">
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
                <p>{character.appearance || "暂无外貌描述"}</p>
                <button className="btn-secondary" disabled={generating != null} onClick={() => generate(character)}>
                  {generating === character.id ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}
                  {character.image_url ? "重新生成" : "生成角色图"}
                </button>
              </div>
            </article>
          ))}
          {!characters.length && <div className="empty-state"><Users size={24} /> 当前项目暂无角色资产。</div>}
        </div>
      </div>
    </div>
  );
}
