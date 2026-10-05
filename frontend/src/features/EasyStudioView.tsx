/**
 * 小白简易工作室：粘贴剧情 → 检查环境 → 一键出片。
 * 西瓜自研文案与交互，不模仿第三方产品。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Film,
  LoaderCircle,
  Rocket,
  Sparkles,
  Wand2,
} from "lucide-react";
import {
  cancelJob,
  easyBootstrap,
  easyChecklist,
  easyMoods,
  easyPipeline,
  getJob,
  getProject,
  getEpisode,
  type EpisodeSummary,
  type ProductionJob,
  type Project,
} from "../api/client";

type Props = {
  username: string;
  current: { drama: Project; episode: EpisodeSummary } | null;
  onOpenEpisode: (drama: Project, episode: EpisodeSummary) => void;
  onGotoPro: (view: string) => void;
};

const STEP_LABEL: Record<string, string> = {
  prereq_llm: "检查 AI 文案",
  script: "准备剧本",
  extract: "提取角色场景",
  character_images: "画角色",
  scene_images: "画场景",
  storyboard: "拆镜头列表",
  storyboard_images: "跳过静帧出图",
  videos: "多图参考生视频(含语音)",
  tts: "语音(模型)",
  bgm: "配乐",
  export: "导出成片",
  cancel: "取消",
};

export default function EasyStudioView({ username, current, onOpenEpisode, onGotoPro }: Props) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [mood, setMood] = useState("warm");
  const [moods, setMoods] = useState<{ id: string; label: string }[]>([]);
  const [checklist, setChecklist] = useState<Awaited<ReturnType<typeof easyChecklist>> | null>(null);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<ProductionJob | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mergedUrl, setMergedUrl] = useState<string | null>(null);
  const [steps, setSteps] = useState<{ step: string; status: string; message: string }[]>([]);
  const pollRef = useRef<number | null>(null);

  const refreshChecklist = useCallback(() => {
    easyChecklist()
      .then(setChecklist)
      .catch(() => setChecklist(null));
  }, []);

  useEffect(() => {
    refreshChecklist();
    easyMoods()
      .then((r) => setMoods(r.moods || []))
      .catch(() => setMoods([]));
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [refreshChecklist]);

  useEffect(() => {
    if (current?.drama.title) setTitle(current.drama.title);
  }, [current?.drama.id, current?.drama.title]);

  const stopPoll = () => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const watchJob = (jobId: number) => {
    stopPoll();
    pollRef.current = window.setInterval(() => {
      getJob(jobId)
        .then((j) => {
          setJob(j);
          const result = j.result as
            | { steps?: { step: string; status: string; message: string }[]; merged_url?: string; error?: string }
            | null
            | undefined;
          if (result?.steps) setSteps(result.steps);
          if (result?.merged_url) setMergedUrl(result.merged_url);
          if (j.status === "completed" || j.status === "failed" || j.status === "cancelled") {
            stopPoll();
            setBusy(false);
            if (j.status === "completed") {
              setNotice(j.message || "出片完成！可以下载或去「成片」页微调。");
              setError(null);
            } else if (j.status === "cancelled") {
              setNotice("已取消");
            } else {
              setError(j.error_msg || result?.error || "出片失败，可点「继续出片」重试（会跳过已完成步骤）");
            }
          }
        })
        .catch(() => undefined);
    }, 2000);
  };

  const startPipeline = async (episodeId: number) => {
    setBusy(true);
    setError(null);
    setNotice("任务已排队，请耐心等待（角色/场景定妆 + 多参考出片较慢，属正常）…");
    setMergedUrl(null);
    setSteps([]);
    try {
      const res = await easyPipeline({
        episode_id: episodeId,
        username,
        skip_existing: true,
        do_export: true,
        transition: "fade",
        bgm_mood: mood,
        auto_bgm: true,
        async_mode: true,
      });
      if (res.async && res.job) {
        setJob(res.job);
        watchJob(res.job.id);
      } else if (res.ok) {
        setBusy(false);
        setMergedUrl(res.merged_url || null);
        setSteps(res.steps || []);
        setNotice("出片完成");
      } else {
        setBusy(false);
        setError(res.error || "流水线未完成");
        setSteps(res.steps || []);
      }
    } catch (e) {
      setBusy(false);
      setError(e instanceof Error ? e.message : "启动失败");
    }
  };

  const handleCreateAndRun = async () => {
    setError(null);
    setNotice(null);
    if (!text.trim() || text.trim().length < 8) {
      setError("请先粘贴一段剧情（至少几句话）");
      return;
    }
    setBusy(true);
    try {
      const boot = await easyBootstrap({
        title: title.trim() || "我的短剧",
        text: text.trim(),
        username,
      });
      const drama = await getProject(boot.drama_id);
      const episode = await getEpisode(boot.episode_id);
      onOpenEpisode(drama, {
        id: episode.id,
        drama_id: episode.drama_id,
        episode_number: episode.episode_number,
        title: episode.title,
        status: episode.status,
        has_content: episode.has_content,
        has_script: episode.has_script,
      });
      setNotice("项目已创建，开始一键出片…");
      await startPipeline(boot.episode_id);
    } catch (e) {
      setBusy(false);
      setError(e instanceof Error ? e.message : "创建失败");
    }
  };

  const handleContinue = async () => {
    if (!current?.episode.id) {
      setError("请先创建项目，或从「项目」打开一集");
      return;
    }
    await startPipeline(current.episode.id);
  };

  const handleCancel = async () => {
    if (!job?.id) return;
    try {
      await cancelJob(job.id);
      setNotice("正在取消…");
    } catch {
      /* ignore */
    }
  };

  const ready = checklist?.ready;
  const tips = checklist?.tips || [];

  return (
    <div className="feature-view easy-studio">
      <div className="feature-header">
        <div>
          <h2>
            <Sparkles size={16} style={{ verticalAlign: -2, marginRight: 6 }} />
            新手一键出片
          </h2>
          <p>文案用 API 写剧本/提示词；角色场景定妆 + 多参考图出片（语音写在提示词里）。高级可切专业模式。</p>
        </div>
        <button type="button" className="btn-secondary" style={{ width: "auto" }} onClick={() => onGotoPro("project")}>
          打开专业模式
        </button>
      </div>

      <div className="feature-body easy-body">
        <section className="easy-card">
          <h3>① 环境检查</h3>
          <p className="easy-hint">{checklist?.summary || "正在检测…"}</p>
          {ready ? (
            <div className="easy-ok">
              <CheckCircle2 size={16} /> {checklist?.headline || "可以开始"}
            </div>
          ) : (
            <ul className="easy-tips">
              {tips.length ? (
                tips.map((t) => (
                  <li key={t}>
                    <AlertTriangle size={14} /> {t}
                  </li>
                ))
              ) : (
                <li>首次使用：① 设置里填语言模型 API（写剧本）② 启动本机 ComfyUI（定妆图 + 多参考出片）。</li>
              )}
            </ul>
          )}
          {checklist?.paths ? (
            <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 8, lineHeight: 1.5 }}>
              检查：文案API {checklist.paths.llm ? "✅" : "❌"} · ComfyUI {checklist.paths.comfy ? "✅" : "❌"} ·
              ffmpeg {checklist.paths.ffmpeg ? "✅" : "—"}
            </div>
          ) : null}
          <div className="easy-actions">
            <button type="button" className="btn-secondary" style={{ width: "auto" }} onClick={refreshChecklist}>
              重新检测
            </button>
            <button type="button" className="btn-secondary" style={{ width: "auto" }} onClick={() => onGotoPro("settings")}>
              去设置（文案 API）
            </button>
            <button type="button" className="btn-secondary" style={{ width: "auto" }} onClick={() => onGotoPro("compute")}>
              去算力（Comfy）
            </button>
          </div>
        </section>

        <section className="easy-card">
          <h3>② 写剧情</h3>
          <label>短剧名称</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="例如：重生之商业传奇"
            disabled={busy}
          />
          <label>粘贴小说 / 大纲 / 一句话剧情</label>
          <textarea
            rows={10}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={"示例：\n男主被公司开除，意外重生到三年前。\n他利用记忆先人一步布局，打脸前上司，拿下大项目……"}
            disabled={busy}
          />
          <label>成片配乐氛围（程序生成，无版权素材）</label>
          <select value={mood} onChange={(e) => setMood(e.target.value)} disabled={busy}>
            {(moods.length ? moods : [{ id: "warm", label: "温暖日常" }]).map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
          <div className="easy-actions" style={{ marginTop: 12 }}>
            <button
              type="button"
              className="btn-primary easy-main-btn"
              disabled={busy}
              onClick={() => void handleCreateAndRun()}
            >
              {busy ? <LoaderCircle className="spin" size={16} /> : <Rocket size={16} />}
              {busy ? " 出片中…" : " 创建并一键出片"}
            </button>
            {current?.episode.id ? (
              <button
                type="button"
                className="btn-secondary"
                style={{ width: "auto" }}
                disabled={busy}
                onClick={() => void handleContinue()}
              >
                <Wand2 size={14} /> 对当前集继续/重试
              </button>
            ) : null}
          </div>
          {current ? (
            <p className="easy-hint" style={{ marginTop: 8 }}>
              当前项目：{current.drama.title} · 第{current.episode.episode_number}集
            </p>
          ) : null}
        </section>

        <section className="easy-card">
          <h3>③ 进度与结果</h3>
          {job ? (
            <div className="easy-progress">
              <div className="easy-progress-bar">
                <div style={{ width: `${job.progress || 0}%` }} />
              </div>
              <div className="easy-progress-meta">
                <span>{job.progress || 0}%</span>
                <span>{job.message || job.status}</span>
                {busy && job.status !== "completed" ? (
                  <button type="button" className="btn-secondary" style={{ width: "auto", padding: "2px 8px" }} onClick={() => void handleCancel()}>
                    取消
                  </button>
                ) : null}
              </div>
            </div>
          ) : (
            <p className="easy-hint">还没开始。填好剧情后点上方绿色按钮即可。</p>
          )}

          {steps.length > 0 ? (
            <ul className="easy-steps">
              {steps.map((s, i) => (
                <li key={`${s.step}-${i}`} data-status={s.status}>
                  <strong>{STEP_LABEL[s.step] || s.step}</strong>
                  <span>{s.message || s.status}</span>
                </li>
              ))}
            </ul>
          ) : null}

          {error ? (
            <div className="feature-notice error" style={{ border: "none", marginTop: 8 }}>
              <AlertTriangle size={14} /> {error}
            </div>
          ) : null}
          {notice ? (
            <div className="feature-notice" style={{ border: "none", marginTop: 8, color: "var(--green-t)" }}>
              <CheckCircle2 size={14} /> {notice}
            </div>
          ) : null}

          {mergedUrl ? (
            <div className="easy-result">
              <Film size={18} />
              <div>
                <strong>成片已就绪</strong>
                <div className="easy-actions" style={{ marginTop: 8 }}>
                  <a className="btn-primary" style={{ width: "auto", textDecoration: "none" }} href={mergedUrl} target="_blank" rel="noreferrer">
                    打开 / 下载 MP4
                  </a>
                  <button type="button" className="btn-secondary" style={{ width: "auto" }} onClick={() => onGotoPro("timeline")}>
                    去成片台微调
                  </button>
                </div>
              </div>
              <video src={mergedUrl} controls style={{ width: "100%", maxHeight: 280, marginTop: 12, borderRadius: 8, background: "#000" }} />
            </div>
          ) : null}
        </section>
      </div>

      <style>{`
        .easy-body {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
          gap: 14px;
          align-content: start;
        }
        .easy-card {
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 14px 16px;
          background: var(--panel);
        }
        .easy-card h3 { margin: 0 0 10px; font-size: 14px; }
        .easy-card label { display: block; margin: 10px 0 4px; color: var(--text2); font-size: 11px; }
        .easy-hint { color: var(--text3); font-size: 12px; line-height: 1.5; margin: 0 0 8px; }
        .easy-ok {
          display: flex; align-items: center; gap: 6px;
          color: var(--green-t); font-size: 13px; margin: 8px 0;
        }
        .easy-tips { list-style: none; padding: 0; margin: 0; }
        .easy-tips li {
          display: flex; gap: 8px; align-items: flex-start;
          font-size: 12px; color: var(--amber); margin-bottom: 8px; line-height: 1.45;
        }
        .easy-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
        .easy-main-btn {
          display: inline-flex; align-items: center; justify-content: center; gap: 6px;
          width: auto; min-width: 160px; padding: 10px 16px; font-size: 13px;
        }
        .easy-progress-bar {
          height: 8px; border-radius: 999px; background: var(--surface); overflow: hidden; margin-bottom: 8px;
        }
        .easy-progress-bar > div {
          height: 100%; background: linear-gradient(90deg, var(--green), var(--blue));
          transition: width .3s ease;
        }
        .easy-progress-meta {
          display: flex; gap: 10px; align-items: center; font-size: 12px; color: var(--text2); flex-wrap: wrap;
        }
        .easy-steps { list-style: none; padding: 0; margin: 12px 0 0; max-height: 220px; overflow: auto; }
        .easy-steps li {
          display: grid; grid-template-columns: 100px 1fr; gap: 8px;
          padding: 6px 8px; border-radius: 6px; font-size: 11px; margin-bottom: 4px;
          background: var(--panel2);
        }
        .easy-steps li[data-status="completed"] { border-left: 3px solid var(--green); }
        .easy-steps li[data-status="skipped"] { border-left: 3px solid var(--text3); opacity: .85; }
        .easy-steps li[data-status="failed"],
        .easy-steps li[data-status="blocked"] { border-left: 3px solid var(--red); }
        .easy-result {
          margin-top: 12px; padding: 12px; border-radius: 10px;
          border: 1px solid rgba(43,178,76,.35); background: rgba(43,178,76,.08);
        }
      `}</style>
    </div>
  );
}
