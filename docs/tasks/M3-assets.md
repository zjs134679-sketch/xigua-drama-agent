# M3 任务：画风库 + 角色/场景素材生成

## 工作目录
仓库根：`F:\waterlmelon\xigua-drama-agent`（Python FastAPI 后端 + React/Vite 前端）。所有改动都在此仓库内完成并提交。

## 现有可复用件（务必复用，勿重造）
- 后端入口 `backend/app/main.py`（`uvicorn app.main:app`，:5678）。Python 解释器用 `C:/xgvenv/Scripts/python.exe`。
- 数据模型 `backend/app/models/domain.py`（已有 `characters / scenes / props / assets / storyboards`，**无 art_styles，需新增**）、`system.py`（`compute_nodes` 等）。
- 算力抽象 `backend/app/services/compute/`：`get_active_node(db)` 返回 `ComputeNode`；`local_comfy.py` 已实现 `text2image`（载入 `app/workflows/*.api.json` → 注入 positive/seed → `/prompt` → 轮询 `/history` → `/view` 下载）。**不要硬编码 ComfyUI URL，一律走 get_active_node。**
- 合规 `backend/app/services/compliance/`：`check(text)->FilterResult`（`level` = pass/yellow/red，红线 `blocked`）；`enforce.record_violation(db, username, result, source)`（掩码留痕）。
- 文生图参考实现：`backend/app/api/compute.py` 的 `/compute/text2image`（先 `check()`，红线 451，再 `get_active_node().text2image()`）。
- 前端模式参考：`frontend/src/api/client.ts`、`frontend/src/App.tsx`（已有活动栏 view 切换骨架）、深色主题 `frontend/src/theme/theme.css`。

## 角色一致性实现说明（自包含，无需外部仓库）
"人在场景里"的多参考一致性思路：先各自出"角色图"和"场景图"；当某镜头需要"角色出现在该场景"时，用 FLUX.1 Kontext 多参考——**参考图顺序：场景图在前、人物图在后**，链式 ReferenceLatent + FluxKontextMultiReferenceLatentMethod(method=index)，提示词含"风格 + 人物名(外貌保持与参考一致) + 场景(location+prompt) + 动作 + 保护语(no text 等)"。Kontext 的 API 格式工作流模板放 `backend/app/workflows/kontext-multiref.api.json`（可新建）。无 Kontext 配置时退普通 flux 单图（`flux-t2i.api.json`）。
> 备注：旧 TS 参考实现在历史个人目录中，**以本说明为准即可，不依赖该仓库**。

## 交付内容
1. **画风库**：新增 `art_styles` 表（`name, prompt_suffix, lora, thumbnail, sort_order` + 时间戳）；CRUD API（`/art-styles`）；前端「画风库」页（列表 + 新增）。
2. **角色/场景素材生成（核心）**：
   - API：`POST /assets/character/generate`、`POST /assets/scene/generate`（入参：目标 id 或自定义 prompt + 可选 `art_style_id` + 可选 `username`）。
   - 服务 `backend/app/services/asset_generation.py`：拼最终 prompt（角色 `appearance` / 场景 `location`+`prompt` + 画风 `prompt_suffix`）→ **过 `check()`（红线 block 返 451 并 `record_violation`；黄线照常生成但响应带 warn）** → `get_active_node(db).text2image()` → 落 `assets` 表，并回写 `image_url/local_path` 到对应 `characters/scenes`。
   - 角色一致性：按上节说明实现（有参考图+场景图→Kontext 多参考；否则退普通 flux）。
3. **前端**：`frontend/src/features/` 下加「角色资产」「画风库」页，复用现有深色主题与 `client.ts` 模式，活动栏对应项可切换。
4. **测试**：`backend/tests/` 加 asset_generation 的 prompt 拼接 + 合规拦截单测。

## 硬约束（必须遵守）
- **任何代码/测试/注释/词库都不得出现真实敏感词**；仓库 `backend/dict/*.txt` 只放占位/演示词（真实词库走云端 `.local`，已 gitignore）。
- 所有送图的最终 prompt **必须过 `check()`**；红线 block 并 `record_violation`（掩码）。
- 复用 `ComputeNode` / `get_active_node`，不要新连 ComfyUI 的硬编码。
- 不破坏现有接口与测试。

## 完成判据
- 后端：`cd backend && C:/xgvenv/Scripts/python.exe -m pytest -q` 全绿。
- 前端：`cd frontend && npm install && npm run build` 通过。
- 完成后 `git add -A` 并按现有中文 message 风格提交。
