import { FormEvent, useEffect, useState } from "react";
import { Image, LoaderCircle, Palette, Plus } from "lucide-react";
import { createArtStyle, listArtStyles, type ArtStyle } from "../api/client";

export default function ArtStylesView() {
  const [styles, setStyles] = useState<ArtStyle[]>([]);
  const [name, setName] = useState("");
  const [suffix, setSuffix] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const load = () => listArtStyles().then(setStyles).catch((e: Error) => setError(e.message));
  useEffect(() => {
    void load();
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    setError("");
    try {
      await createArtStyle({ name: name.trim(), prompt_suffix: suffix.trim(), lora: null, thumbnail: null, sort_order: styles.length });
      setName("");
      setSuffix("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "新增失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="feature-view">
      <div className="feature-header">
        <div><h2>画风库</h2><p>统一角色、场景与镜头的视觉语言</p></div>
        <span className="pill" style={{ border: "1px solid var(--border2)", color: "var(--text2)" }}><Palette size={13} /> {styles.length} 种画风</span>
      </div>
      <div className="feature-body two-column">
        <section>
          <div className="asset-grid">
            {styles.map((style) => (
              <article className="asset-card" key={style.id}>
                <div className="asset-preview">
                  {style.thumbnail ? <img src={style.thumbnail} alt={style.name} /> : <Image size={28} />}
                </div>
                <div className="asset-content">
                  <strong>{style.name}</strong>
                  <p>{style.prompt_suffix || "未设置提示词后缀"}</p>
                  {style.lora && <span className="pill">LoRA · {style.lora}</span>}
                </div>
              </article>
            ))}
            {!styles.length && <div className="empty-state">暂无画风，请在右侧新增。</div>}
          </div>
        </section>
        <aside className="feature-aside">
          <h3><Plus size={15} /> 新增画风</h3>
          <form onSubmit={submit}>
            <label>名称</label>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：写实电影感" />
            <label>提示词后缀</label>
            <textarea rows={6} value={suffix} onChange={(e) => setSuffix(e.target.value)} placeholder="描述光影、色彩、材质与镜头质感" />
            <button className="btn-primary" disabled={saving || !name.trim()}>
              {saving ? <LoaderCircle className="spin" size={14} /> : <Plus size={14} />} 保存画风
            </button>
            {error && <p className="form-error">{error}</p>}
          </form>
        </aside>
      </div>
    </div>
  );
}
