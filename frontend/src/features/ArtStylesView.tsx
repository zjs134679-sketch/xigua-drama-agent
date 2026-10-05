import { FormEvent, useEffect, useState } from "react";
import { CheckCircle2, LoaderCircle, Palette, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import {
  createArtStyle,
  deleteArtStyle,
  listArtStyles,
  listProjects,
  seedArtStyles,
  updateArtStyle,
  updateStyleBible,
  type ArtStyle,
  type Project,
} from "../api/client";
import { projectStyleId, projectStyleName } from "./styleHelpers";

export default function ArtStylesView({
  currentDramaId = null,
}: {
  currentDramaId?: number | null;
}) {
  const [styles, setStyles] = useState<ArtStyle[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [applyDramaId, setApplyDramaId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [suffix, setSuffix] = useState("");
  const [saving, setSaving] = useState(false);
  const [seeding, setSeeding] = useState(false);
  const [error, setError] = useState("");
  const [manuals, setManuals] = useState<Record<number, string>>({});
  const [suffixes, setSuffixes] = useState<Record<number, string>>({});
  const [savingManual, setSavingManual] = useState<number | null>(null);
  const [applyingId, setApplyingId] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [notice, setNotice] = useState("");

  const applyRows = (rows: ArtStyle[]) => {
    setStyles(rows);
    setManuals(() => {
      const next: Record<number, string> = {};
      rows.forEach((s) => {
        next[s.id] = s.constraint_manual ?? "";
      });
      return next;
    });
    setSuffixes(() => {
      const next: Record<number, string> = {};
      rows.forEach((s) => {
        next[s.id] = s.prompt_suffix ?? "";
      });
      return next;
    });
  };

  const load = () =>
    listArtStyles()
      .then(applyRows)
      .catch((e: Error) => setError(e.message));

  const refreshProjects = () =>
    listProjects()
      .then((rows) => {
        setProjects(rows);
        if (currentDramaId != null && rows.some((r) => r.id === currentDramaId)) {
          setApplyDramaId(currentDramaId);
        } else if (rows.length && applyDramaId == null) {
          setApplyDramaId(rows[0].id);
        }
      })
      .catch(() => undefined);

  useEffect(() => {
    void load();
    void refreshProjects();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentDramaId]);

  const activeProject = projects.find((p) => p.id === applyDramaId) ?? null;
  const activeStyleId = projectStyleId(activeProject);
  const activeStyleName = projectStyleName(activeProject);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    setError("");
    try {
      await createArtStyle({
        name: name.trim(),
        prompt_suffix: suffix.trim(),
        lora: null,
        thumbnail: null,
        sort_order: styles.length,
      });
      setName("");
      setSuffix("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "新增失败");
    } finally {
      setSaving(false);
    }
  };

  const saveStyle = async (style: ArtStyle) => {
    setSavingManual(style.id);
    setNotice("");
    try {
      await updateArtStyle(style.id, {
        prompt_suffix: suffixes[style.id] ?? "",
        constraint_manual: manuals[style.id] ?? "",
      });
      setNotice(`已保存编辑「${style.name}」（仅更新预设内容，不会自动绑定到项目）`);
      await load();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSavingManual(null);
    }
  };

  const applyToProject = async (style: ArtStyle) => {
    if (applyDramaId == null) {
      setNotice("请先选择要绑定的项目（或从顶部进入某个项目）");
      return;
    }
    setApplyingId(style.id);
    setNotice("");
    try {
      const updated = await updateStyleBible(applyDramaId, {
        art_style_id: style.id,
        visual_name: style.name,
      });
      setProjects((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
      setNotice(`已将「${style.name}」设为项目《${updated.title}》的画风（角色/场景/道具/分镜/视频均跟随）`);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "设为项目画风失败");
    } finally {
      setApplyingId(null);
    }
  };

  const removeStyle = async (style: ArtStyle) => {
    if (!window.confirm(`确定删除画风「${style.name}」？`)) return;
    setDeleting(style.id);
    setNotice("");
    try {
      await deleteArtStyle(style.id);
      setNotice(`已删除「${style.name}」`);
      await load();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "删除失败");
    } finally {
      setDeleting(null);
    }
  };

  const runSeed = async () => {
    setSeeding(true);
    setError("");
    setNotice("");
    try {
      const result = await seedArtStyles(false);
      applyRows(result.styles);
      setNotice(
        `已同步预设：新增 ${result.inserted}，补全 ${result.updated}（库内共 ${result.styles.length} 种）`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "同步预设失败");
    } finally {
      setSeeding(false);
    }
  };

  return (
    <div className="feature-view">
      <div className="feature-header">
        <div>
          <h2>画风库</h2>
          <p>
            这里是<strong>预设编辑</strong>：改提示词后缀/约束手册后点「保存编辑」。
            要让出图生效，请点卡片上的<strong>设为项目画风</strong>，或在「项目」里选画风。
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <select
            value={applyDramaId ?? ""}
            onChange={(e) => setApplyDramaId(e.target.value ? Number(e.target.value) : null)}
            title="设为项目画风时绑定到哪个项目"
            style={{ maxWidth: 220 }}
          >
            {!projects.length && <option value="">暂无项目</option>}
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.title}</option>
            ))}
          </select>
          <span className="pill" style={{ border: "1px solid var(--border2)", color: "var(--text2)" }}>
            当前项目画风：{activeStyleName || "未设置"}
          </span>
          <button className="btn-secondary" style={{ width: "auto" }} disabled={seeding} onClick={() => void runSeed()}>
            {seeding ? <LoaderCircle className="spin" size={13} /> : <RefreshCw size={13} />} 同步预设
          </button>
          <span className="pill" style={{ border: "1px solid var(--border2)", color: "var(--text2)" }}>
            <Palette size={13} /> {styles.length} 种
          </span>
        </div>
      </div>
      {notice && <div className="feature-notice">{notice}</div>}
      {error && <div className="feature-notice" style={{ color: "var(--red-t)" }}>{error}</div>}
      <div className="feature-body two-column">
        <section>
          <div className="asset-grid">
            {styles.map((style) => {
              const isActive = activeStyleId === style.id || activeStyleName === style.name;
              return (
                <article
                  className="asset-card"
                  key={style.id}
                  style={isActive ? { outline: "2px solid var(--green)", outlineOffset: 0 } : undefined}
                >
                  {style.thumbnail ? (
                    <div className="asset-preview">
                      <img src={style.thumbnail} alt={style.name} />
                    </div>
                  ) : null}
                  <div className="asset-content" style={{ padding: 12 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                      <strong style={{ fontSize: 15 }}>
                        {style.name}
                        {isActive ? (
                          <span style={{ marginLeft: 8, fontSize: 11, color: "var(--green-t)", fontWeight: 500 }}>
                            · 项目在用
                          </span>
                        ) : null}
                      </strong>
                      <button
                        type="button"
                        className="btn-secondary"
                        style={{ width: "auto", padding: "4px 8px" }}
                        disabled={deleting != null}
                        onClick={() => void removeStyle(style)}
                        title="删除此预设"
                      >
                        {deleting === style.id ? <LoaderCircle className="spin" size={13} /> : <Trash2 size={13} />}
                      </button>
                    </div>
                    <label style={{ fontSize: 11, color: "var(--text3)", marginTop: 8 }}>提示词后缀（出图追加）</label>
                    <textarea
                      rows={3}
                      value={suffixes[style.id] ?? ""}
                      onChange={(e) => setSuffixes((m) => ({ ...m, [style.id]: e.target.value }))}
                      style={{ resize: "vertical", fontSize: 12, lineHeight: 1.5 }}
                      placeholder="中文风格锚点…"
                    />
                    {style.lora && <span className="pill">LoRA · {style.lora}</span>}
                    <label style={{ fontSize: 11, color: "var(--text3)", marginTop: 4 }}>风格约束手册（可编辑）</label>
                    <textarea
                      rows={6}
                      value={manuals[style.id] ?? ""}
                      onChange={(e) => setManuals((m) => ({ ...m, [style.id]: e.target.value }))}
                      style={{ resize: "vertical", fontSize: 12, lineHeight: 1.45 }}
                    />
                    <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                      <button
                        type="button"
                        className="btn-primary"
                        style={{ width: "auto", flex: 1, minWidth: 120 }}
                        disabled={applyingId != null || applyDramaId == null}
                        onClick={() => void applyToProject(style)}
                        title="写入当前选中项目的 style_bible，全项目出图跟随"
                      >
                        {applyingId === style.id ? <LoaderCircle className="spin" size={13} /> : <CheckCircle2 size={13} />}
                        {isActive ? "已是项目画风" : "设为项目画风"}
                      </button>
                      <button
                        type="button"
                        className="btn-secondary"
                        style={{ width: "auto" }}
                        disabled={savingManual === style.id}
                        onClick={() => void saveStyle(style)}
                        title="只保存预设文字，不改变项目绑定"
                      >
                        {savingManual === style.id ? <LoaderCircle className="spin" size={13} /> : <Save size={13} />}
                        保存编辑
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
            {!styles.length && (
              <div style={{ gridColumn: "1 / -1", padding: 24, color: "var(--text3)", fontSize: 13 }}>
                暂无画风。点击上方「同步预设」导入默认库，或在右侧手动新增。
              </div>
            )}
          </div>
        </section>
        <aside>
          <form className="panel" onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <h3 style={{ margin: 0, fontSize: 14, display: "flex", alignItems: "center", gap: 6 }}>
              <Plus size={15} /> 新增画风预设
            </h3>
            <p style={{ margin: 0, fontSize: 12, color: "var(--text3)" }}>新增后仍需「设为项目画风」才会用于出图。</p>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="名称，如：水墨国风" required />
            <textarea
              rows={4}
              value={suffix}
              onChange={(e) => setSuffix(e.target.value)}
              placeholder="提示词后缀（出图追加）"
            />
            <button className="btn-primary" type="submit" disabled={saving || !name.trim()}>
              {saving ? <LoaderCircle className="spin" size={14} /> : <Plus size={14} />} 保存预设
            </button>
          </form>
        </aside>
      </div>
    </div>
  );
}
