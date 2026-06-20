import { useEffect, useState } from "react";
import { AlertTriangle, Image, LoaderCircle, Mountain, Sparkles, Wand2 } from "lucide-react";
import {
  extractFromEpisode,
  generateSceneAsset,
  listArtStyles,
  listProjects,
  listScenes,
  type ArtStyle,
  type Project,
  type SceneAsset,
} from "../api/client";

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
  const [generating, setGenerating] = useState<number | null>(null);
  const [extracting, setExtracting] = useState(false);
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

  const loadScenes = (id: number) => listScenes(id).then(setScenes).catch((e: Error) => setNotice(e.message));
  useEffect(() => {
    if (projectId != null) loadScenes(projectId);
  }, [projectId]);

  const generate = async (scene: SceneAsset) => {
    setGenerating(scene.id);
    setNotice("");
    try {
      const result = await generateSceneAsset({ scene_id: scene.id, art_style_id: styleId });
      setNotice(result.warn ? "场景图已生成，提示词包含需关注内容。" : "场景图生成完成。");
      if (projectId != null) await loadScenes(projectId);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "生成失败");
    } finally {
      setGenerating(null);
    }
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

  return (
    <div className="feature-view">
      <div className="feature-header">
        <div><h2>场景 / 造景</h2><p>从剧本提取场景，生成纯背景参考图</p></div>
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
          {scenes.map((scene) => (
            <article className="asset-card" key={scene.id}>
              <div className="asset-preview">
                {scene.image_url ? <img src={scene.image_url} alt={scene.location ?? ""} /> : <Image size={30} />}
              </div>
              <div className="asset-content">
                <strong>{scene.location || "未命名场景"}</strong>
                <span>{scene.time || "未设置时间"}</span>
                <p>{scene.prompt || "暂无背景提示词"}</p>
                <button className="btn-secondary" disabled={generating != null} onClick={() => generate(scene)}>
                  {generating === scene.id ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}
                  {scene.image_url ? "重新生成" : "生成场景图"}
                </button>
              </div>
            </article>
          ))}
          {!scenes.length && <div className="empty-state"><Mountain size={24} /> 当前项目暂无场景。点右上「AI 提取角色/场景」，从分集剧本自动提取。</div>}
        </div>
      </div>
    </div>
  );
}
