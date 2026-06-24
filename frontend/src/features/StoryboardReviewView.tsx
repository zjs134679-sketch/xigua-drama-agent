import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, ClipboardCheck, Edit3, Loader2, RefreshCw, Sparkles, Wand2 } from "lucide-react";
import {
  getLatestStoryboardReview,
  remediateStoryboardReview,
  runStoryboardReview,
  type EpisodeSummary,
  type Project,
  type ReviewSeverity,
  type StoryboardReviewReport,
} from "../api/client";

const SEVERITY: Record<ReviewSeverity, { label: string; mark: string }> = {
  severe: { label: "严重", mark: "🔴" },
  medium: { label: "中等", mark: "🟡" },
  minor: { label: "轻微", mark: "⚪" },
};

const GRADE_TEXT: Record<StoryboardReviewReport["grade"], string> = {
  A: "可直接使用",
  B: "小修后可用",
  C: "需要较大修改",
  D: "建议重做",
};

export default function StoryboardReviewView({
  current,
  username,
  onOpenStoryboard,
}: {
  current: { drama: Project; episode: EpisodeSummary } | null;
  username: string;
  onOpenStoryboard?: () => void;
}) {
  const episode = current?.episode ?? null;
  const [report, setReport] = useState<StoryboardReviewReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [remediating, setRemediating] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [filter, setFilter] = useState<ReviewSeverity | "all">("all");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    setReport(null);
    setError("");
    setNotice("");
    setFilter("all");
    if (!episode) return;
    setLoading(true);
    getLatestStoryboardReview(episode.id)
      .then((value) => {
        setReport(value);
        setInstruction(value?.instruction ?? "");
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "读取审核报告失败"))
      .finally(() => setLoading(false));
  }, [episode?.id]);

  const issues = useMemo(
    () => (report?.issues ?? []).filter((item) => filter === "all" || item.severity === filter),
    [report, filter],
  );

  const review = async () => {
    if (!episode) return;
    setReviewing(true);
    setError("");
    setNotice("");
    try {
      const next = await runStoryboardReview({
        episode_id: episode.id,
        username,
        instruction: instruction.trim() || undefined,
      });
      setReport(next);
      setFilter("all");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "分镜审核失败");
    } finally {
      setReviewing(false);
    }
  };

  const remediate = async () => {
    if (!report) return;
    setRemediating(true);
    setError("");
    setNotice("");
    try {
      const result = await remediateStoryboardReview(report.id, {
        username,
        instruction: instruction.trim() || undefined,
      });
      setReport(result.review);
      setNotice(`Agent 已整改 ${result.changed_count} 个镜头。你可以重新审核，或进入分镜台继续手动修改。`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "一键整改失败");
    } finally {
      setRemediating(false);
    }
  };

  if (!episode) {
    return <div className="empty-state review-no-project">请先在「项目」里选择一个分集，再进行分镜审核。</div>;
  }

  return (
    <div className="feature-view review-view">
      <div className="feature-header">
        <div>
          <h2><ClipboardCheck size={16} /> 分镜审核 Agent</h2>
          <p>{current?.drama.title} · 第{episode.episode_number}集 · 对照剧本、资产与生产规范独立审片</p>
        </div>
        <div className="review-header-actions">
          {report && <button className="review-fix" onClick={remediate} disabled={remediating || reviewing || !report.issues.length}>
            {remediating ? <Loader2 size={14} className="spin" /> : <Wand2 size={14} />}
            {remediating ? "整改中…" : "Agent 一键整改"}
          </button>}
          <button className="review-run" onClick={review} disabled={reviewing || remediating || loading}>
            {reviewing ? <Loader2 size={14} className="spin" /> : report ? <RefreshCw size={14} /> : <Sparkles size={14} />}
            {reviewing ? "监制审核中…" : report ? "重新审核" : "开始审核"}
          </button>
        </div>
      </div>

      {error && <div className="feature-notice error"><AlertTriangle size={14} /> {error}</div>}
      {notice && <div className="feature-notice review-success"><CheckCircle2 size={14} /> {notice}</div>}

      <div className="review-layout">
        <main className="review-main">
          {loading ? (
            <div className="empty-state"><Loader2 size={18} className="spin" /> 正在读取审核报告</div>
          ) : !report ? (
            <div className="review-empty">
              <ClipboardCheck size={42} />
              <h3>还没有审核报告</h3>
              <p>Agent 将逐项检查台词完整性、人物连续性、资产关联、VO 音画同步、时长、景别和拆镜粒度。</p>
              <button className="review-run" onClick={review} disabled={reviewing}><Sparkles size={14} /> 开始首次审核</button>
            </div>
          ) : (
            <>
              <section className={`review-summary grade-${report.grade.toLowerCase()}`}>
                <div className="review-grade"><strong>{report.grade}</strong><span>{GRADE_TEXT[report.grade]}</span></div>
                <div className="review-overview">
                  <h3>审核总评</h3>
                  <p>{report.summary}</p>
                  <small>{report.created_at ? new Date(report.created_at).toLocaleString() : ""}{report.model ? ` · ${report.model}` : ""}</small>
                </div>
                <div className="review-counts">
                  {(Object.keys(SEVERITY) as ReviewSeverity[]).map((severity) => (
                    <button key={severity} className={filter === severity ? "active" : ""} onClick={() => setFilter(filter === severity ? "all" : severity)}>
                      <b>{report.counts[severity] ?? 0}</b><span>{SEVERITY[severity].mark} {SEVERITY[severity].label}</span>
                    </button>
                  ))}
                </div>
              </section>

              {report.remediation && (
                <section className="review-remediation">
                  <div><Wand2 size={16} /><strong>已执行一键整改</strong><span>{report.remediation.summary}</span></div>
                  <button className="btn-secondary" onClick={onOpenStoryboard}><Edit3 size={13} /> 去分镜台手动修改</button>
                  <button className="btn-secondary" onClick={review} disabled={reviewing}><RefreshCw size={13} /> 整改后重新审核</button>
                </section>
              )}

              <section className="review-report">
                <div className="review-report-head">
                  <h3>问题清单</h3>
                  <span>{filter === "all" ? `全部 ${issues.length}` : `${SEVERITY[filter].label} ${issues.length}`}</span>
                </div>
                {!issues.length ? (
                  <div className="review-passed"><CheckCircle2 size={20} /> 当前筛选项全部通过</div>
                ) : issues.map((issue) => (
                  <article className={`review-issue severity-${issue.severity}`} key={issue.id}>
                    <div className="review-issue-index">{issue.id}</div>
                    <div className="review-issue-body">
                      <div className="review-issue-meta">
                        <span>{SEVERITY[issue.severity].mark} {SEVERITY[issue.severity].label}</span>
                        <strong>{issue.category}</strong>
                        {!!issue.storyboard_numbers.length && <em>镜头 {issue.storyboard_numbers.join("、")}</em>}
                      </div>
                      <p>{issue.problem}</p>
                      <div className="review-suggestion"><b>建议</b>{issue.suggestion}</div>
                    </div>
                  </article>
                ))}
              </section>

              {!!report.decisions.length && (
                <section className="review-decisions">
                  <h3>需要你决定</h3>
                  {report.decisions.map((item, index) => <p key={`${index}-${item}`}>{index + 1}. {item}</p>)}
                </section>
              )}
            </>
          )}
        </main>

        <aside className="review-aside">
          <h3>补充审核要求</h3>
          <p>可指定本次特别关注的角色、剧情段落或平台规范。</p>
          <textarea rows={7} value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="例如：重点检查李明在第 3–8 镜是否始终在场；台词必须完全忠于原文。" disabled={reviewing} />
          <div className="review-rule-list">
            <h4>默认检查</h4>
            <span>台词完整性与剧本覆盖</span>
            <span>在场人物不消失</span>
            <span>人物外观不混入提示词</span>
            <span>VO 音画同步</span>
            <span>景别视角与连贯性</span>
            <span>时长与拆镜粒度</span>
          </div>
        </aside>
      </div>
    </div>
  );
}
