# Codex 任务：素材台增强（#1 干净人物 + Toonflow ③④⑤⑥⑦⑧）

> 执行者：Codex（effort = xhigh）。验收/提交：Claude。
> **只写实现**。不要启动任何服务器、不要 `npm install/build/dev`、不要 `git commit`、不要跑长 verify。
> 允许：`python -m py_compile`、读代码。前端类型检查由 Claude 跑 `tsc -b`。

## 仓库与约定
- 后端：`backend/`（FastAPI + SQLAlchemy 2，端口 5678，SQLite 在 `backend/data/`）。
- 前端：`frontend/`（React+Vite+TS，端口 5173/5199）。
- 复用件已存在：`frontend/src/components/{BatchBar.tsx,useSelection.ts,useBatchRun.ts}`、各 `*AssetsView.tsx`。
- **SQLite 无迁移框架**：`Base.metadata.create_all` 只建新表，**不会给已存在的表加新列**。任何新增列必须在 `backend/app/core/db.py`（或 main 启动钩子）里写**幂等 ensure-column**（用 `PRAGMA table_info` 查，缺了再 `ALTER TABLE ADD COLUMN`），否则现有 dev 库会 `no such column` 崩。新表交给 create_all 即可。
- **硬红线（务必遵守）**：① 任何真实敏感词严禁出现在代码/测试/注释/输出；合规仍只在"提示词→图片"那一刻 `check(prompt)` 拦截。② `api_key` 一律掩码，绝不回传明文。③ 不改既有合规词库文件。④ 不破坏现有工作流与已通过的接口。

---

## 工作流顺序（按此优先级实现；A→D 比 A→G 全坏更可取）

### A. #1 生成的人物**不能带环境、不能带文字**（最高优先，最快收益）
现状：`backend/app/services/asset_generation.py` 只在**正向**提示词尾部加 `PROTECTION_PROMPT="no text, no watermark, no logo"`，且 `_generate` 调 `ImageJob` 时**没传 negative**，人物图因此常带背景/水印（见用户截图：满屏环境+右下水印+左上印章字）。

要求：
1. 新增**角色专用负向词**常量，至少含：
   `text, words, letters, chinese characters, watermark, signature, logo, stamp, seal, caption, subtitle, ui, frame, border, multiple people, crowd, scenery, landscape, background scene, environment, buildings, complex background, blurry, lowres`
2. 新增**通用负向词**常量（场景/分镜/道具用）：去掉 `scenery/landscape/background/environment/multiple people`（这些它们需要），保留 `text, watermark, signature, logo, stamp, seal, caption, subtitle, ui, frame, border, lowres, blurry`。
3. 角色正向提示词补：`solo, single character, plain solid color background, clean studio background, character reference, centered`（在 `build_character_prompt` 里，PROTECTION 之外追加；但**不要**覆盖用户已编辑的 `image_prompt`——优先级仍是 用户 full_prompt > 已存 image_prompt > 自动拼，只在"自动拼"分支加这些；负向词**始终**注入，与提示词来源无关）。
4. `_generate(...)` 与 `generate_storyboard_image(...)` 调 `ImageJob(...)` 时**传入 `negative=`**：角色用角色负向词，场景/分镜/道具用通用负向词。`ImageJob` 已支持 `negative` 字段，`local_comfy._inject` 已会把 negative 写进工作流的负向 CLIPTextEncode——只要传值即可。
5. 验收点（Claude 跑）：开 ComfyUI 后生成角色图，肉眼为**纯背景、无文字水印的人物**；负向词确实进了 `/prompt` 请求体。

### B. ③ 道具出图页
- 新建 `frontend/src/features/PropAssetsView.tsx`，**镜像 `SceneAssetsView.tsx`**（复用 BatchBar/useSelection/useBatchRun + 可编辑提示词 + AI 提取 + 批量），数据用道具：
  - 列表：`GET /projects/{drama_id}/props`（已存在，返回 id/name/type/description/prompt；**给它补 `image_url`**字段返回）。
  - 出图：新增 `POST /assets/prop/generate`（body：`prop_id, prompt?, art_style_id?, username, node_id?, resolution?, extra?`）→ 新增 `generate_prop_asset(...)` in `asset_generation.py`（仿 `generate_scene_asset`，category="prop"，target=Prop，用通用负向词）。
  - 改提示词：`PATCH /projects/props/{prop_id}`（仿 `update_scene_prompt`，写 `Prop.prompt`）。
  - client.ts 加：`listProps/generatePropAsset/updatePropPrompt`，`PropAsset` 类型含 `image_url`。
- 入口：`App.tsx` 给 `ViewId` 加 `"props"`，RAIL 加一项（icon 用 `Package` lucide，label "道具"），render 分支挂 `PropAssetsView`（传 currentDramaId/currentEpisodeId/username）。
- 道具来源已有：`/extract` 会从剧本抽道具入库，提取按钮复用。

### C. ④ 每素材**分辨率 / 模型**可选
- **分辨率**：定义共享预设（前后端一致），建议：
  - `portrait_768x1024`（竖屏，角色默认）、`square_1024x1024`、`landscape_1024x576`（场景/分镜默认）、`hd_portrait_896x1152`。
  - 后端 generate 系列请求加 `resolution: str | None`；解析成 width/height 传入 `ImageJob(width=, height=)`。给 `resolution` 做白名单校验，非法回落到该类别默认。
- **模型**：复用算力节点为"出图模型"下拉（**不要造假模型**）：
  - 前端拉 `GET /compute/nodes`，下拉显示各节点 name+type；选中传 `node_id`。
  - 后端 `_generate` 系列加 `node_id: int | None`，有则用该节点，否则 `get_active_node`。在 `app/services/compute/registry.py` 加 `get_node(db, node_id)`（缺失/停用回落 active）。
- 前端：每张卡提示词下方加两个 `<select>`（分辨率、模型），默认值按类别。批量沿用每卡各自的选择。
- 落库：每次出图写一条 `ImageGeneration`（见 D），把 model/size/width/height/seed 记上。

### D. ⑤ 历史多版图（每素材保留**最近 3 版**）
- **复用已存在的 `image_generations` 表**（已有 character_id/scene_id/prop_id/storyboard_id/prompt/image_url/local_path/model/size/seed/status/completed_at）。**不要新建表**。
- 每次**成功**出图，在 `_generate`/`generate_storyboard_image` 里插入一条 `ImageGeneration`（填对应 FK + image_type=category + prompt + image_url + local_path + model + size + width + height + seed + status="completed" + completed_at=now）。插入后**剪枝**：该 target 的 completed 记录只保留 id 最大的 3 条，多余的删除（DB 行即可，本地图片文件先不删，避免误删）。
- 接口：
  - `GET /assets/history?target_type=character|scene|prop|storyboard&target_id=ID` → 返回最近 3 条（id desc）的 `{id,image_url,local_path,prompt,created_at}`。
  - `POST /assets/history/{image_gen_id}/use` → 把对应 target 的 `image_url/local_path` 设为该历史行（"回退/选用某一版"），返回 target 新状态。
- 前端：每张卡图片下加一条**最多 3 个缩略图**的历史条；点某版 = 调 use 设为当前并刷新；当前版高亮。client.ts 加 `listAssetHistory/useAssetHistory`。

### E. ⑥ 统一附加指令
- 每个素材台头部加一个输入框「附加指令（追加到本页全部提示词）」。
- 前端在**单个/批量**出图时，把该文本作为 `extra` 字段随请求发送（不写进保存的提示词，保持已存提示词干净）。
- 后端 generate 系列加 `extra: str | None`，在**合规检查之前**把 `extra` 追加到最终 prompt 尾部（与提示词一起过 `check()` 与负向词一起送图）。
- 不持久化（本会话 UI 状态即可），注释说明。

### F. ⑦ 一键绑定音频
- `Character` 已有 `voice_style/voice_provider/voice_sample_url`；`ai_voices` 表已存在。
- **音色来源**：若库里 `ai_voices` 为空，则**种子一批通用预设**（provider="preset"，voice_id 如 `preset_male_young/preset_male_steady/preset_female_sweet/preset_female_mature/preset_old_male`，voice_name 中文名）。种子是数据插入（启动时若空表则插），不是真 TTS。
- 接口：
  - `GET /voices` → 列 ai_voices。
  - `PATCH /projects/characters/{id}/voice` → body `{voice_id, voice_provider}` 写入角色。
  - `POST /projects/{drama_id}/assign-voices`（一键绑定）→ 若配了 LLM 用 `voice_assigner` skill 按性别/年龄/性格分配；**没配 LLM 则用确定性回落**（按 role/personality 关键词的简单规则轮转预设），给每个还没音色的角色分配并落库；返回每角色分配结果。
- 前端：角色台头部「一键绑定音频」按钮 + 每卡一个音色 `<select>`（值=voice_id），改选即 PATCH。client.ts 加 `listVoices/assignVoices/updateCharacterVoice`，`CharacterAsset` 加 `voice_id?`。
- 不要求真合成音频；这是**绑定元数据**供后续 TTS/时间线用，注释写清楚。

### G. ⑧ 风格约束手册
- `ArtStyle` **新增列** `constraint_manual: Text`（**这是唯一的新增列——务必加 ensure-column 幂等迁移**）。
- 画风库页 `frontend/src/features/ArtStylesView.tsx`：每个画风加可编辑「约束手册」textarea + 保存；`PUT /art-styles/{id}` 已支持部分更新，给 `ArtStyleUpdate` 加 `constraint_manual` 字段，`style_view` 返回它。
- 生成时：选了画风且其 `constraint_manual` 非空 → 在自动拼提示词时把手册作为**风格约束**追加（正向，PROTECTION 之前）；用户传了 full_prompt 时不强加（尊重用户）。注释说明这是"风格一致性约束"。
- （进阶可选，不强求）：若 LLM 已配，提供 `POST /art-styles/{id}/check-prompt`，让 LLM 判断 prompt 是否与手册冲突并给建议——**没把握就跳过，先做存储+追加**。

---

## 交付与自检
- 后端：`python -m py_compile $(对改动的 .py)` 不报错；新接口在 `app/main.py` 已注册路由。
- 不跑前端构建/服务器/commit。完成后**列出所有改动文件**与**每个文件做了什么**，并明确 A–G 各项**完成 / 部分 / 未做**。
- 若某项卡住，**跳过并标注原因**，不要硬塞假数据或写死 mock。
