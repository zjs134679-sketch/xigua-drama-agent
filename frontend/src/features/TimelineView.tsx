import { useEffect, useMemo, useRef, useState } from "react";
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
  Video,
  Volume2,
} from "lucide-react";
import {
  exportTimeline,
  generateStoryboardTTS,
  generateVideo,
  getTimeline,
  listCharacters,
  listEpisodes,
  listProjects,
  listStoryboards,
  listVoices,
  previewVoice,
  saveTimeline,
  updateStoryboard,
  updateCharacterVoice,
  type CharacterAsset,
  type EpisodeSummary,
  type Project,
  type TimelineClip,
  type TimelineDocument,
  type TimelineTrack,
  type TimelineTrackName,
  type VoiceRecord,
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

export default function TimelineView({
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
  const [generatingVideo, setGeneratingVideo] = useState<number | null>(null);
  const [videoPrompts, setVideoPrompts] = useState<Record<number, string>>({});
  const [characters, setCharacters] = useState<CharacterAsset[]>([]);
  const [voices, setVoices] = useState<VoiceRecord[]>([]);
  const [speakingChars, setSpeakingChars] = useState<Record<number, number | null>>({});
  const [previewingVoice, setPreviewingVoice] = useState(false);
  const [generatingVoice, setGeneratingVoice] = useState<number | null>(null);
  const autoSaveTimer = useRef<number | null>(null);

  useEffect(() => {
    listProjects()
      .then((rows) => {
        setProjects(rows);
        const preferred = currentDramaId != null && rows.some((r) => r.id === currentDramaId) ? currentDramaId : rows[0]?.id ?? null;
        setProjectId(preferred);
      })
      .catch((error: Error) => setNotice(error.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentDramaId]);

  useEffect(() => {
    if (projectId == null) {
      setEpisodes([]);
      setEpisodeId(null);
      return;
    }
    listEpisodes(projectId)
      .then((rows) => {
        setEpisodes(rows);
        const preferred = currentEpisodeId != null && rows.some((r) => r.id === currentEpisodeId) ? currentEpisodeId : rows[0]?.id ?? null;
        setEpisodeId(preferred);
      })
      .catch((error: Error) => setNotice(error.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, currentEpisodeId]);

  useEffect(() => {
    if (projectId == null) { setCharacters([]); return; }
    listCharacters(projectId).then(setCharacters).catch(() => setCharacters([]));
  }, [projectId]);

  useEffect(() => {
    listVoices().then(setVoices).catch(() => setVoices([]));
  }, []);

  useEffect(() => {
    if (episodeId == null) { setSpeakingChars({}); return; }
    listStoryboards(episodeId)
      .then((rows) => setSpeakingChars(Object.fromEntries(rows.map((s) => [s.id, s.speaking_character_id]))))
      .catch(() => setSpeakingChars({}));
  }, [episodeId]);

  useEffect(() => () => {
    if (autoSaveTimer.current != null) window.clearTimeout(autoSaveTimer.current);
  }, []);

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
  const selectedVoice = useMemo(
    () => timeline?.tracks.voiceover.clips.find((clip) => clip.storyboard_id === selectedId) ?? null,
    [timeline, selectedId],
  );
  const selectedCharacter = useMemo(() => {
    if (selected?.storyboard_id == null) return null;
    const characterId = speakingChars[selected.storyboard_id];
    return characters.find((character) => character.id === characterId) ?? null;
  }, [characters, selected, speakingChars]);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const playVoice = (url: string | null | undefined) => {
    if (!url) return;
    if (!audioRef.current) audioRef.current = new Audio();
    audioRef.current.src = mediaUrl(url);
    void audioRef.current.play().catch(() => undefined);
  };

  const reorder = (offset: number) => {
    if (!timeline || selectedId == null) return;
    const clips = [...timeline.tracks.video.clips];
    const index = clips.findIndex((clip) => clip.storyboard_id === selectedId);
    const target = index + offset;
    if (index < 0 || target < 0 || target >= clips.length) return;
    [clips[index], clips[target]] = [clips[target], clips[index]];
    setTimeline(synchronise(timeline, clips));
  };

  const scheduleStoryboardSave = (
    storyboardId: number,
    changes: { dialogue?: string; duration?: number },
    document: TimelineDocument,
  ) => {
    if (autoSaveTimer.current != null) window.clearTimeout(autoSaveTimer.current);
    autoSaveTimer.current = window.setTimeout(async () => {
      try {
        await updateStoryboard(storyboardId, changes);
        if (episodeId != null) await saveTimeline(episodeId, document);
      } catch (error) {
        setNoticeKind("error");
        setNotice(error instanceof Error ? error.message : "自动保存失败");
      }
    }, 500);
  };

  const updateDuration = (duration: number) => {
    if (!timeline || selectedId == null) return;
    const clips = timeline.tracks.video.clips.map((clip) =>
      clip.storyboard_id === selectedId ? { ...clip, duration: Math.max(duration, 0) } : clip,
    );
    const next = synchronise(timeline, clips);
    setTimeline(next);
    scheduleStoryboardSave(selectedId, { duration: Math.max(1, Math.round(duration)) }, next);
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
    const synchronised = synchronise(next, next.tracks.video.clips);
    setTimeline(synchronised);
    scheduleStoryboardSave(selectedId, { dialogue: subtitleText }, synchronised);
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

  const reloadTimeline = async () => {
    if (episodeId == null) return;
    const doc = await getTimeline(episodeId);
    setTimeline(doc);
  };

  const setSpeakingCharacter = async (storyboardId: number, characterId: number | null) => {
    const previous = speakingChars[storyboardId] ?? null;
    setSpeakingChars((prev) => ({ ...prev, [storyboardId]: characterId }));
    try {
      await updateStoryboard(storyboardId, { speaking_character_id: characterId });
      setNoticeKind("normal");
      setNotice(characterId == null ? "已取消说话角色绑定" : "说话角色已绑定");
    } catch (error) {
      setSpeakingChars((prev) => ({ ...prev, [storyboardId]: previous }));
      setNoticeKind("error");
      setNotice(error instanceof Error ? error.message : "绑定说话角色失败");
    }
  };

  const bindSelectedVoice = async (voiceId: string) => {
    if (!selectedCharacter) return;
    const voice = voices.find((item) => item.voice_id === voiceId);
    if (!voice) return;
    try {
      await updateCharacterVoice(selectedCharacter.id, voice.voice_id, voice.provider);
      setCharacters((rows) => rows.map((row) => row.id === selectedCharacter.id
        ? { ...row, voice_id: voice.voice_id, voice_provider: voice.provider }
        : row));
      setNoticeKind("normal");
      setNotice(`已为“${selectedCharacter.name}”绑定音色“${voice.voice_name}”`);
    } catch (error) {
      setNoticeKind("error");
      setNotice(error instanceof Error ? error.message : "绑定音色失败");
    }
  };

  const auditionSelectedVoice = async () => {
    if (!selectedCharacter?.voice_id) return;
    setPreviewingVoice(true);
    try {
      const source = selectedSubtitle?.subtitle_text ?? selected?.subtitle_text ?? "";
      const text = source.replace(/^[^：:\n]{1,12}[：:]\s*/gm, "").slice(0, 60);
      const result = await previewVoice(selectedCharacter.voice_id, text || undefined);
      playVoice(result.audio_url);
    } catch (error) {
      setNoticeKind("error");
      setNotice(error instanceof Error ? error.message : "试听失败");
    } finally {
      setPreviewingVoice(false);
    }
  };

  const generateVoiceForSelected = async () => {
    if (selected?.storyboard_id == null) return;
    if (!selectedCharacter) {
      setNoticeKind("error");
      setNotice("请先选择说话角色");
      return;
    }
    if (!selectedCharacter.voice_id) {
      setNoticeKind("error");
      setNotice(`请先给“${selectedCharacter.name}”选择音色`);
      return;
    }
    const storyboardId = selected.storyboard_id;
    const dialogue = selectedSubtitle?.subtitle_text ?? selected.subtitle_text ?? "";
    if (!dialogue.trim()) {
      setNoticeKind("error");
      setNotice("当前片段没有台词");
      return;
    }
    setGeneratingVoice(storyboardId);
    setNotice("");
    try {
      await updateStoryboard(storyboardId, {
        speaking_character_id: selectedCharacter.id,
        dialogue: dialogue.trim(),
      });
      await generateStoryboardTTS(storyboardId, username);
      await reloadTimeline();
      setSelectedId(storyboardId);
      setNoticeKind("normal");
      setNotice(`“${selectedCharacter.name}”的当前片段配音已生成`);
    } catch (error) {
      setNoticeKind("error");
      setNotice(error instanceof Error ? error.message : "当前片段配音失败");
    } finally {
      setGeneratingVoice(null);
    }
  };

  // 单镜图 → 视频（LTX i2v）。LTX 片段宜短，时长 2~6s。
  const generateOneVideo = (storyboardId: number, durationSec: number) =>
    generateVideo({
      storyboard_id: storyboardId,
      username,
      duration: Math.min(Math.max(Math.round(durationSec) || 5, 2), 6),
      resolution: "1024x576",
      prompt: videoPrompts[storyboardId]?.trim() || undefined,
    });

  const generateVideoForSelected = async () => {
    if (selected?.storyboard_id == null || episodeId == null) return;
    setGeneratingVideo(selected.storyboard_id);
    setNotice("");
    setNoticeKind("normal");
    try {
      const result = await generateOneVideo(selected.storyboard_id, selected.duration);
      if (result.status === "completed" && result.video_url) {
        await reloadTimeline();
        setNotice("镜头视频已生成");
      } else {
        setNoticeKind("error");
        setNotice(result.error_msg || "视频生成失败");
      }
    } catch (error) {
      setNoticeKind("error");
      setNotice(error instanceof Error ? error.message : "视频生成失败");
    } finally {
      setGeneratingVideo(null);
    }
  };

  const generateTalkingVideoForSelected = async () => {
    if (selected?.storyboard_id == null || episodeId == null) return;
    if (!selectedVoice?.audio_url) {
      setNoticeKind("error");
      setNotice("请先生成当前片段配音，再生成说话视频");
      return;
    }
    setGeneratingVideo(selected.storyboard_id);
    setNotice("");
    try {
      const result = await generateVideo({
        storyboard_id: selected.storyboard_id,
        username,
        duration: Math.min(Math.max(Math.round(selected.duration) || 3, 2), 5),
        resolution: "1024x576",
        reference_mode: "audio_driven",
        model: "ltx2.3-audio-driven",
        prompt: videoPrompts[selected.storyboard_id]?.trim()
          || "single visible speaker, medium close-up, speaking naturally, clear face, synchronized mouth movement, subtle head motion",
      });
      if (result.status === "completed" && result.video_url) {
        await reloadTimeline();
        setSelectedId(selected.storyboard_id);
        setNoticeKind("normal");
        setNotice("LTX‑2.3 音频驱动说话视频已生成");
      } else {
        setNoticeKind("error");
        setNotice(result.error_msg || "说话视频生成失败");
      }
    } catch (error) {
      setNoticeKind("error");
      setNotice(error instanceof Error ? error.message : "说话视频生成失败");
    } finally {
      setGeneratingVideo(null);
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
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 5 }}>
            <span style={{ flex: 1, fontSize: 10, color: "var(--text3)" }}>修改后自动保存；括号内动作不会朗读</span>
            <button
              type="button"
              className="btn-secondary"
              style={{ width: "auto", padding: "3px 8px", fontSize: 10 }}
              disabled={!selected || !(selectedSubtitle?.subtitle_text ?? selected?.subtitle_text ?? "")}
              onClick={() => updateSubtitle("")}
            >
              清空字幕
            </button>
          </div>
          <label>说话角色</label>
          <select
            disabled={!selected}
            value={selected?.storyboard_id != null ? (speakingChars[selected.storyboard_id] ?? "") : ""}
            onChange={(event) => {
              const sbId = selected?.storyboard_id;
              if (sbId != null) void setSpeakingCharacter(sbId, event.target.value ? Number(event.target.value) : null);
            }}
          >
            <option value="">请选择说话角色</option>
            {characters.map((c) => (
              <option key={c.id} value={c.id}>{c.name}{c.voice_id ? "" : "（未绑音色）"}</option>
            ))}
          </select>
          <label>角色音色</label>
          <select
            disabled={!selectedCharacter}
            value={selectedCharacter?.voice_id ?? ""}
            onChange={(event) => void bindSelectedVoice(event.target.value)}
          >
            <option value="">请选择音色</option>
            {voices.map((voice) => (
              <option key={`${voice.provider}:${voice.voice_id}`} value={voice.voice_id}>
                {voice.voice_name} · {voice.description || voice.voice_id}
              </option>
            ))}
          </select>
          <div style={{ marginTop: 5, fontSize: 10, lineHeight: 1.45, color: "var(--text3)" }}>
            {!selectedCharacter
              ? "先选择说话角色，才能绑定和试听角色音色。"
              : !selectedCharacter.voice_id
                ? `“${selectedCharacter.name}”还没有绑定音色，请在上方选择。`
                : `试听会用“${selectedCharacter.name}”的音色朗读当前字幕。`}
          </div>
          <div className="reorder-buttons" style={{ marginTop: 8 }}>
            <button
              className="btn-secondary"
              disabled={!selectedCharacter?.voice_id || previewingVoice}
              onClick={() => void auditionSelectedVoice()}
            >
              {previewingVoice ? <LoaderCircle className="spin" size={14} /> : <Volume2 size={14} />} 试听音色
            </button>
            <button
              className="btn-primary"
              disabled={selected?.storyboard_id == null || generatingVoice != null}
              onClick={() => void generateVoiceForSelected()}
            >
              {generatingVoice === selected?.storyboard_id ? <LoaderCircle className="spin" size={14} /> : <Mic2 size={14} />}
              {generatingVoice === selected?.storyboard_id ? "生成中…" : "生成本片段"}
            </button>
          </div>
          <div className="reorder-buttons">
            <button className="btn-secondary" disabled={!selected || selected.index === 0} onClick={() => reorder(-1)}><ChevronLeft size={14} /> 前移</button>
            <button className="btn-secondary" disabled={!selected || selected.index === (timeline?.tracks.video.clips.length ?? 0) - 1} onClick={() => reorder(1)}>后移 <ChevronRight size={14} /></button>
          </div>
          <button
            className="btn-secondary"
            style={{ marginTop: 8, width: "100%" }}
            disabled={!selectedVoice?.audio_url}
            onClick={() => playVoice(selectedVoice?.audio_url)}
            title={selectedVoice?.audio_url ? "播放本片段已生成的配音" : "本片段还没有生成配音"}
          >
            <Volume2 size={14} /> {selectedVoice?.audio_url ? "播放已生成配音" : "暂无已生成配音"}
          </button>
          <label style={{ marginTop: 10 }}>视频提示词（留空＝用镜头图提示词，有台词自动加「说话」）</label>
          <textarea
            rows={3}
            disabled={!selected}
            value={selected?.storyboard_id != null ? videoPrompts[selected.storyboard_id] ?? "" : ""}
            onChange={(event) => {
              const sbId = selected?.storyboard_id;
              if (sbId != null) setVideoPrompts((prev) => ({ ...prev, [sbId]: event.target.value }));
            }}
            placeholder="例如：近景，人物边说边微微点头，口型自然，镜头缓推"
          />
          <button
            className="btn-primary"
            style={{ marginTop: 10, width: "100%" }}
            disabled={selected?.storyboard_id == null || !selected?.thumbnail || generatingVideo != null}
            onClick={() => void generateVideoForSelected()}
            title="用该镜头的图，经 LTX 图生视频生成视频片段"
          >
            {generatingVideo === selected?.storyboard_id ? <LoaderCircle className="spin" size={14} /> : <Video size={14} />}
            {generatingVideo === selected?.storyboard_id ? " 生成中（LTX 出片较慢）…" : selected?.video_url ? " 重新生成视频" : " 生成视频"}
          </button>
          <button
            className="btn-primary"
            style={{ marginTop: 8, width: "100%", background: "#178f68" }}
            disabled={selected?.storyboard_id == null || !selected?.thumbnail || !selectedVoice?.audio_url || generatingVideo != null}
            onClick={() => void generateTalkingVideoForSelected()}
            title="用当前分镜图和已生成的 TTS 配音，通过 LTX‑2.3 联合音视频模型生成说话画面"
          >
            {generatingVideo === selected?.storyboard_id ? <LoaderCircle className="spin" size={14} /> : <Mic2 size={14} />}
            {generatingVideo === selected?.storyboard_id ? " 音频驱动生成中…" : " 生成说话视频（LTX‑2.3）"}
          </button>
          {!selectedVoice?.audio_url && selected && (
            <div style={{ marginTop: 5, fontSize: 10, lineHeight: 1.45, color: "var(--text3)" }}>
              先在上方生成本片段配音，才可启用音频驱动说话视频。
            </div>
          )}
          {!selected?.thumbnail && selected && (
            <div style={{ marginTop: 5, fontSize: 10, lineHeight: 1.45, color: "var(--amber)" }}>
              当前片段没有分镜图片，请先到“分镜台”给该镜头出图，再生成视频。
            </div>
          )}
        </aside>

        <section className="timeline-editor">
          <div className="timeline-toolbar">
            <span><Film size={14} /> 总时长 {formatTime(timeline?.duration ?? 0)}</span>
            <div />
            <span style={{ fontSize: 11, color: "var(--text3)" }}>选择片段后，在右侧绑定角色与音色并单独生成</span>
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
                        <strong>{name === "subtitle"
                          ? clip.subtitle_text || "空字幕"
                          : name === "voiceover"
                            ? characters.find((character) => character.id === speakingChars[clip.storyboard_id ?? -1])?.name || `配音 ${clip.index + 1}`
                            : `片段 ${clip.index + 1}`}</strong>
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
