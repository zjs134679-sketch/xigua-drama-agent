import { FormEvent, useEffect, useState } from "react";
import { Image, LoaderCircle, Palette, Plus, Save } from "lucide-react";
import { createArtStyle, listArtStyles, updateArtStyle, type ArtStyle } from "../api/client";

export default function ArtStylesView() {
  const [styles, setStyles] = useState<ArtStyle[]>([]);
  const [name, setName] = useState("");
  const [suffix, setSuffix] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [manuals, setManuals] = useState<Record<number, string>>({});
  const [savingManual, setSavingManual] = useState<number | null>(null);
  const [notice, setNotice] = useState("");

  const load = () =>
    listArtStyles()
      .then((rows) => {
        setStyles(rows);
        setManuals((prev) => {
          const next = { ...prev };
          rows.forEach((s) => {
            if (next[s.id] === undefined) next[s.id] = s.constraint_manual ?? "";
          });
          return next;
        });
      })
      .catch((e: Error) => setError(e.message));
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

  const saveManual = async (style: ArtStyle) => {
    setSavingManual(style.id);
    setNotice("");
    try {
      await updateArtStyle(style.id, { constraint_manual: manuals[style.id] ?? "" });
      setNotice(`已保存「${style.name}」的约束手册`);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSavingManual(null);
    }
  };

  return (
    <div className="feature-view">
      <div className="feature-header">
        <div><h2>画风库</h2><p>统一角色、场景与镜头的视觉语言；约束手册会在出图时强制追加</p></div>
        <span className="pill" style={{ border: "1px solid var(--border2)", color: "var(--text2)" }}><Palette size={13} /> {styles.length} 种画风</span>
      </div>
      {notice && <div className="feature-notice">{notice}</div>}
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
                  <label style={{ fontSize: 11, color: "var(--text3)", marginTop: 4 }}>风格约束手册（可编辑，出图时追加）</label>
                  <textarea
                    rows={4}
                    value={manuals[style.id] ?? ""}
                    onChange={(e) => setManuals((m) => ({ ...m, [style.id]: e.target.value }))}
                    style={{ resize: "vertical", fontSize: 12, lineHeight: 1.5 }}
                    placeholder="例如：唐代历史正剧，禁止现代/西方元素，服饰建筑须符合盛唐，写实电影质感…"
                  />
                  <button
                    className="btn-secondary"
                    style={{ width: "auto", padding: "5px 12px" }}
                    disabled={savingManual != null}
                    onClick={() => saveManual(style)}
                    title="只保存约束手册"
                  >
                    {savingManual === style.id ? <LoaderCircle className="spin" size={13} /> : <Save size={13} />} 保存约束
                  </button>
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
