import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Captions,
  ChevronLeft,
  ChevronRight,
  Download,
  Film,
  LoaderCircle,
  Mic2,
  Music2,
  Play,
  Save,
  Video,
} from "lucide-react";
import {
  exportTimeline,
  getTimeline,
  listEpisodes,
  listProjects,
  saveTimeline,
  type EpisodeSummary,
  type Project,
  type TimelineClip,
  type TimelineDocument,
  type TimelineTrack,
  type TimelineTrackName,
} from "../api/client";

const TRACKS: { name: TimelineTrackName; label: string; color: string; icon: typeof Video }[] = [
  { name: "video", label: "视频", color: "var(--blue)", icon: Video },
  { name: "voiceover", label: "配音", color: "#3fb58e", icon: Mic2 },
  { name: "subtitle", label: "字幕", color: "var(--amber)", icon: Captions },
  { name: "music", label: "音乐", color: "#a879e8", icon: Music2 },
];

function formatTime(seconds: number) {
  const value = Math.max(seconds, 0);
  const minutes = Math.floor(value / 60);
  return `${String(minutes).padStart(2, "0")}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
}

function mediaUrl(url: string | null | undefined) {
  if (!url) return "";
  return url.startsWith("/oss/") ? `/api${url}` : url;
}

function synchronise(document: TimelineDocument, videoClips: TimelineClip[]): TimelineDocument {
  let start = 0;
  const timing = new Map<number, { index: number; start: number; duration: number }>();
  const video = videoClips.map((clip, index) => {
    const next = { ...clip, index, start, duration: Math.max(clip.duration, 0) };
    if (next.storyboard_id != null) timing.set(next.storyboard_id, next);
    start += next.duration;
    return next;
  });
  const syncTrack = (track: TimelineTrack) => ({
    ...track,
    clips: track.clips
      .filter((clip) => clip.storyboard_id != null && timing.has(clip.storyboard_id))
      .sort((left, right) => timing.get(left.storyboard_id!)!.index - timing.get(right.storyboard_id!)!.index)
      .map((clip) => ({ ...clip, ...timing.get(clip.storyboard_id!)! })),
  });
  return {
    ...document,
    duration: start,
    tracks: {
      ...document.tracks,
      video: { ...document.tracks.video, clips: video },
      voiceover: syncTrack(document.tracks.voiceover),
      subtitle: syncTrack(document.tracks.subtitle),
    },
  };
}

export default function TimelineView() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [episodes, setEpisodes] = useState<EpisodeSummary[]>([]);
  const [episodeId, setEpisodeId] = useState<number | null>(null);
  const [timeline, setTimeline] = useState<TimelineDocument | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [notice, setNotice] = useState("");
  const [noticeKind, setNoticeKind] = useState<"normal" | "error">("normal");
  const [mergedUrl, setMergedUrl] = useState("");

  useEffect(() => {
    listProjects()
      .then((rows) => {
        setProjects(rows);
        setProjectId(rows[0]?.id ?? null);
      })
      .catch((error: Error) => setNotice(error.message));
  }, []);

  useEffect(() => {
    if (projectId == null) {
      setEpisodes([]);
      setEpisodeId(null);
      return;
    }
    listEpisodes(projectId)
      .then((rows) => {
        setEpisodes(rows);
        setEpisodeId(rows[0]?.id ?? null);
      })
      .catch((error: Error) => setNotice(error.message));
  }, [projectId]);

  useEffect(() => {
    if (episodeId == null) {
      setTimeline(null);
      return;
    }
    let active = true;
    setLoading(true);
    setNotice("");
    setMergedUrl("");
    getTimeline(episodeId)
      .then((document) => {
        if (!active) return;
        setTimeline(document);
        setSelectedId(document.tracks.video.clips[0]?.storyboard_id ?? null);
      })
      .catch((error: Error) => active && setNotice(error.message))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [episodeId]);

  const selected = useMemo(
    () => timeline?.tracks.video.clips.find((clip) => clip.storyboard_id === selectedId) ?? null,
    [timeline, selectedId],
  );
  const selectedSubtitle = useMemo(
    () => timeline?.tracks.subtitle.clips.find((clip) => clip.storyboard_id === selectedId) ?? null,
    [timeline, selectedId],
  );

  const reorder = (offset: number) => {
    if (!timeline || selectedId == null) return;
    const clips = [...timeline.tracks.video.clips];
    const index = clips.findIndex((clip) => clip.storyboard_id === selectedId);
    const target = index + offset;
    if (index < 0 || target < 0 || target >= clips.length) return;
    [clips[index], clips[target]] = [clips[target], clips[index]];
    setTimeline(synchronise(timeline, clips));
  };

  const updateDuration = (duration: number) => {
    if (!timeline || selectedId == null) return;
    const clips = timeline.tracks.video.clips.map((clip) =>
      clip.storyboard_id === selectedId ? { ...clip, duration: Math.max(duration, 0) } : clip,
    );
    setTimeline(synchronise(timeline, clips));
  };

  const updateSubtitle = (subtitleText: string) => {
    if (!timeline || selectedId == null || !selected) return;
    const exists = timeline.tracks.subtitle.clips.some((clip) => clip.storyboard_id === selectedId);
    const clips = exists
      ? timeline.tracks.subtitle.clips.map((clip) =>
          clip.storyboard_id === selectedId ? { ...clip, subtitle_text: subtitleText } : clip,
        )
      : [...timeline.tracks.subtitle.clips, { ...selected, video_url: null, audio_url: null, subtitle_text: subtitleText }];
    const next = {
      ...timeline,
      tracks: {
        ...timeline.tracks,
        subtitle: { ...timeline.tracks.subtitle, clips },
      },
    };
    setTimeline(synchronise(next, next.tracks.video.clips));
  };

  const toggleTrack = (name: TimelineTrackName) => {
    if (!timeline) return;
    setTimeline({
      ...timeline,
      tracks: {
        ...timeline.tracks,
        [name]: { ...timeline.tracks[name], enabled: !timeline.tracks[name].enabled },
      },
    });
  };

  const save = async () => {
    if (!timeline || episodeId == null) return null;
    setSaving(true);
    setNotice("");
    try {
      const saved = await saveTimeline(episodeId, timeline);
      setTimeline(saved);
      setNoticeKind("normal");
      setNotice("时间线已保存");
      return saved;
    } catch (error) {
      setNoticeKind("error");
      setNotice(error instanceof Error ? error.message : "保存失败");
      return null;
    } finally {
      setSaving(false);
    }
  };

  const exportMovie = async () => {
    if (!timeline || episodeId == null) return;
    setExporting(true);
    setNotice("");
    setMergedUrl("");
    try {
      const saved = await save();
      if (!saved) return;
      const result = await exportTimeline(episodeId);
      if (result.status === "completed" && result.merged_url) {
        setMergedUrl(mediaUrl(result.merged_url));
        setNoticeKind("normal");
        setNotice("成片导出完成");
      } else {
        setNoticeKind("error");
        setNotice(result.error || "导出失败");
        if (result.blocked && result.clip_index != null) setSelectedId(saved.tracks.video.clips[result.clip_index]?.storyboard_id ?? null);
      }
    } catch (error) {
      setNoticeKind("error");
      setNotice(error instanceof Error ? error.message : "导出失败");
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="feature-view timeline-view">
      <div className="feature-header">
        <div><h2>成片时间线</h2><p>编排视频、配音、字幕与音乐轨道</p></div>
        <div className="feature-filters">
          <select value={projectId ?? ""} onChange={(event) => setProjectId(Number(event.target.value))}>
            {!projects.length && <option value="">暂无项目</option>}
            {projects.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
          </select>
          <select value={episodeId ?? ""} onChange={(event) => setEpisodeId(Number(event.target.value))}>
            {!episodes.length && <option value="">暂无分集</option>}
            {episodes.map((episode) => <option key={episode.id} value={episode.id}>第 {episode.episode_number} 集 · {episode.title}</option>)}
          </select>
        </div>
      </div>

      {notice && <div className={`feature-notice${noticeKind === "error" ? " error" : ""}`}><AlertTriangle size={14} /> {notice}</div>}

      <div className="timeline-workspace">
        <section className="timeline-preview">
          <div className="preview-stage">
            {selected?.video_url ? (
              <video key={selected.video_url} src={mediaUrl(selected.video_url)} poster={mediaUrl(selected.thumbnail)} controls />
            ) : selected?.thumbnail ? (
              <img src={mediaUrl(selected.thumbnail)} alt="镜头预览" />
            ) : (
              <div className="preview-empty"><Play size={34} /><span>{loading ? "正在加载时间线" : "选择视频片段进行预览"}</span></div>
            )}
          </div>
          <div className="preview-meta">
            <span>镜头 {selected ? String(selected.index + 1).padStart(2, "0") : "—"}</span>
            <span>{formatTime(selected?.start ?? 0)} / {formatTime(timeline?.duration ?? 0)}</span>
          </div>
        </section>

        <aside className="timeline-inspector">
          <h3>片段属性</h3>
          <label>时长（秒）</label>
          <input
            type="number"
            min="0"
            step="0.1"
            disabled={!selected}
            value={selected?.duration ?? 0}
            onChange={(event) => updateDuration(Number(event.target.value))}
          />
          <label>字幕文本</label>
          <textarea
            rows={5}
            disabled={!selected}
            value={selectedSubtitle?.subtitle_text ?? selected?.subtitle_text ?? ""}
            onChange={(event) => updateSubtitle(event.target.value)}
            placeholder="输入当前镜头字幕"
          />
          <div className="reorder-buttons">
            <button className="btn-secondary" disabled={!selected || selected.index === 0} onClick={() => reorder(-1)}><ChevronLeft size={14} /> 前移</button>
            <button className="btn-secondary" disabled={!selected || selected.index === (timeline?.tracks.video.clips.length ?? 0) - 1} onClick={() => reorder(1)}>后移 <ChevronRight size={14} /></button>
          </div>
        </aside>

        <section className="timeline-editor">
          <div className="timeline-toolbar">
            <span><Film size={14} /> 总时长 {formatTime(timeline?.duration ?? 0)}</span>
            <div />
            <button className="toolbar-button" disabled={!timeline || saving || exporting} onClick={() => void save()}>
              {saving ? <LoaderCircle className="spin" size={14} /> : <Save size={14} />} 保存
            </button>
          </div>
          <div className="timeline-scroll">
            {timeline && TRACKS.map(({ name, label, color, icon: Icon }) => {
              const track = timeline.tracks[name];
              return (
                <div className={`timeline-track${track.enabled ? "" : " muted"}`} key={name}>
                  <button className="track-label" onClick={() => toggleTrack(name)} title={track.enabled ? "关闭轨道" : "开启轨道"}>
                    <Icon size={14} /><span>{label}</span><i className={track.enabled ? "on" : ""} />
                  </button>
                  <div className="track-lane">
                    {track.clips.map((clip, clipIndex) => (
                      <button
                        key={`${name}-${clip.storyboard_id ?? clipIndex}`}
                        className={`timeline-clip${clip.storyboard_id === selectedId ? " selected" : ""}`}
                        style={{ flexGrow: Math.max(clip.duration, 0.2), background: color }}
                        onClick={() => clip.storyboard_id != null && setSelectedId(clip.storyboard_id)}
                      >
                        <strong>{name === "subtitle" ? clip.subtitle_text || "空字幕" : `片段 ${clip.index + 1}`}</strong>
                        <span>{clip.duration.toFixed(1)}s</span>
                      </button>
                    ))}
                    {!track.clips.length && <span className="track-empty">暂无{label}素材</span>}
                  </div>
                </div>
              );
            })}
            {!timeline && !loading && <div className="empty-state">请选择包含分镜的分集。</div>}
          </div>
        </section>
      </div>

      <div className="timeline-export-bar">
        {mergedUrl ? (
          <div className="export-result"><Film size={15} /><span>成片已就绪</span><a href={mergedUrl} target="_blank" rel="noreferrer"><Play size={14} /> 播放</a><a href={mergedUrl} download><Download size={14} /> 下载</a></div>
        ) : <span>导出时将按当前轨道顺序合成 MP4</span>}
        <button className="export-button" disabled={!timeline?.tracks.video.clips.length || exporting || saving} onClick={() => void exportMovie()}>
          {exporting ? <LoaderCircle className="spin" size={15} /> : <Film size={15} />}
          {exporting ? "正在导出" : "导出成片"}
        </button>
      </div>
    </div>
  );
}
