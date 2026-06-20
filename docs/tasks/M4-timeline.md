# M4 任务：剪映式时间线 + 配音/字幕轨 + 成片导出

## 工作目录
仓库根：`F:\waterlmelon\xigua-drama-agent`（在会话根 F:\waterlmelon 内，沙箱可写）。所有改动都在此仓库内完成。

## ⚠️ 执行约束（重要，避免卡死）
- **只写实现代码与单测文件**。**不要**运行 `npm install`、`npm run build`、`vite`、dev server、`tauri`、`git commit`，**不要**跑任何长时间/交互式命令（会在沙箱里卡住）。
- 允许快速只读自检（如 `python -c "import app.main"`），但不强制；完成后**列出改动文件清单即可**，验证与提交由人工接手。

## 现有可复用件
- 后端入口 `backend/app/main.py`（include_router 模式）。Python 用 `C:/xgvenv/Scripts/python.exe`。
- 数据模型 `backend/app/models/domain.py`：`episodes`、`storyboards`（字段含 `storyboard_number, video_url, tts_audio_url, subtitle_url, dialogue, bgm_prompt, sound_effect, duration, composed_video_url`）、`video_merges`（`scenes` JSON、`merged_url`、`status`）、`assets`。
- 合规 `backend/app/services/compliance/`：`check(text)->FilterResult`（红线 `blocked`）、`enforce.record_violation`。
- 算力/路由模式参考 `backend/app/api/compute.py`、`assets.py`；前端模式参考 `frontend/src/features/CharacterAssetsView.tsx`、`frontend/src/App.tsx`（已有活动栏 view 切换）、`frontend/src/api/client.ts`、深色主题 `theme.css`。

## 交付内容

### 1. 后端：时间线数据 + API（`backend/app/api/timeline.py`）
- `GET /timeline/{episode_id}`：从该集 `storyboards`（按 `storyboard_number` 排序）组装时间线，返回多轨结构：
  - 轨道：`video`（每个 storyboard 的 `video_url` 或 `composed_video_url`）、`voiceover`（`tts_audio_url`）、`subtitle`（`dialogue`/`subtitle_url`）、`music`（集级 BGM，可空）。
  - 每个 clip：`{storyboard_id, index, start, duration, video_url, audio_url, subtitle_text, thumbnail}`，`start` 按前序 duration 累加。
- `PUT /timeline/{episode_id}`：保存编辑后的时间线（clip 顺序、`duration`、`subtitle_text`、轨道开关），持久化为 `video_merges.scenes`（JSON）或新增 `timelines` 表（择一，简洁即可）。
- `POST /timeline/{episode_id}/export`：合成成片，见下。

### 2. 后端：成片导出服务（`backend/app/services/video_compose.py`，用 ffmpeg）
- 步骤：按时间线顺序取各 clip 视频 → ffmpeg `concat` 拼接 → 混入 `voiceover`（TTS）与 `music`（BGM，可选、低音量）→ 烧录/挂载 `subtitle`（可用 ffmpeg `subtitles`/`drawtext` 或生成 .srt 再 mux）→ 输出 MP4 到 `backend/data/oss/`。
- **合规**：导出前对所有 `subtitle_text` 逐条过 `check()`；命中红线 → 不导出，返回 451 + 指出是哪个 clip（`storyboard_id`）+ `record_violation`。
- **ffmpeg 调用安全**：用 `subprocess`/`asyncio` 以**参数列表**方式调用（绝不拼 shell 字符串、绝不 `shell=True`）。
- ffmpeg 不存在时：返回清晰错误（提示用户安装 ffmpeg），不要崩溃。
- 成功后：写 `video_merges`（status=completed、merged_url、duration）并回写 `episodes.video_url`。
- 导出可能耗时：可同步实现（先简单），并在响应里带 `status`/`merged_url`/`error`。

### 3. 前端：剪映式时间线页（`frontend/src/features/TimelineView.tsx`）
- 多轨时间线：视频 / 配音 / 字幕 / 音乐 四轨，clip 宽度按 duration 比例；可选中 clip、调顺序（上/下或拖拽，简单可用即可）、编辑字幕文本与时长。
- 顶部预览区（选中 clip 显示其视频/首帧）；底部「导出成片」按钮 → 调 `POST /timeline/{id}/export`，显示进度/结果（成功给下载/播放链接，红线给拦截提示）。
- 复用现有深色主题 token 与 `client.ts` 模式；接入 `App.tsx` 活动栏「成片」view 切换。
- `frontend/src/api/client.ts` 增 `getTimeline / saveTimeline / exportTimeline`。

### 4. 测试（`backend/tests/test_timeline.py`）
- 时间线从 storyboards 组装（start 累加、轨道分配）正确。
- 导出前字幕命中红线 → 拦截（用占位词，**严禁真实敏感词**）。
- ffmpeg 命令以参数列表构造（可 mock ffmpeg，不真跑）。

## 硬约束
- **任何代码/测试/注释/词库都不得出现真实敏感词**。
- 字幕文本导出前必须过 `check()`；红线拦截 + 掩码 `record_violation`。
- ffmpeg 一律参数列表、禁 `shell=True`。
- 复用现有模型与 `get_active_node` 等既有件，不破坏现有接口与测试。

## 完成判据（人工验收，Codex 不必自己跑）
- `cd backend && C:/xgvenv/Scripts/python.exe -m pytest -q` 全绿。
- `cd frontend && npm run build` 通过。
- 起 ffmpeg + 已有素材时，`POST /timeline/{id}/export` 能产出 MP4 落 `data/oss/`；含红线字幕的导出被 451 拦截。
