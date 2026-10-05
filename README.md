# 西瓜短剧Agent（国内版）

> 面向中国合规市场的 AI 短剧生产软件。深色专业 UI，**本地 ComfyUI 出图出片** + **LLM API 写剧本/提示词** + 合规红黄线。

---

## 技术栈

| 层次 | 技术 |
|------|------|
| 后端主服务 | Python FastAPI（端口 5678） |
| 前端 | React + Vite + TypeScript（端口 5173） |
| 桌面壳 | Tauri（需 Rust 环境） |
| 云端认证 / 授权加密 | FastAPI（:8100）卡密+机器绑定+能力票 |
| **文案** | LLM API（DeepSeek / 通义 / 豆包等 OpenAI 兼容）— 剧本、分镜、提示词 |
| **画面** | 本地 / 远程 **ComfyUI**（MiniMax H3 文生图 / 图生视频） |
| 合规 | 红线硬拦截、黄线提示、三次红线封号 |
| 任务队列 | `production_jobs` 异步队列 + Comfy 全局互斥锁 |

---

## 核心流水线

```
小说/剧本(LLM) → 事件图谱 → 改编分集 → 抽角色/场景/道具
→ 分镜拆解(LLM) → 审查 → 出图(Comfy H3) → 定稿出片(Comfy H3 i2v)
→ 成片时间线 → 导出 MP4
```

| 环节 | 谁干 | 说明 |
|------|------|------|
| 剧本 / 分镜 / 提示词 | LLM API | 设置页配置 Key |
| 角色/场景/分镜图 | ComfyUI | 本机 :8188 |
| 镜头视频 | ComfyUI | MiniMax H3 i2v，约 3–5s/镜 |
| 语音 | 视频模型 | 定稿出片时写入提示词，由模型直接生成（已取消独立 TTS） |
| 合镜 | ffmpeg | 保留镜头原生音轨 + 转场 + 程序化配乐 |

**4060 8G 建议**：任务队列同时只跑 1 个 Comfy 任务。

---

## 目录结构

```
xigua-drama-agent/
├── backend/        # FastAPI 主后端
├── frontend/       # React 前端
├── src-tauri/      # Tauri 桌面壳
├── auth-server/    # 已迁移 → 密码管理/auth-server
├── docs/           # 规格与里程碑
└── scripts/        # 启动/打包脚本
```

---

## 本地运行

### 一键启动

双击 `scripts\启动.bat`。

- 后端文档：http://127.0.0.1:5678/docs  
- 前端：http://localhost:5173  

### 手动启动

```bash
# 后端
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 5678

# 前端
cd frontend
npm run dev
```

---

## 已具备能力

- [x] **新手一键出片**（文案 API + Comfy 全流程，可续跑）
- [x] **负向/保护语配置表**（`negative_packs.json`，题材开关）+ 出图前 `xg_prompt_grid` 编排
- [x] **video_prompt 与 duration 时长对齐**；一键出片失败/拆镜后自动 `xg_shot_audit`
- [x] **成片淡入转场** + **程序化配乐**（无第三方曲库版权）
- [x] 新手 / 专业 UI 切换（默认新手精简侧栏）
- [x] 异步任务队列 / 取消 / 进度（`/jobs`）
- [x] 小说事件图谱 + 按事件改编（`/events`）
- [x] 项目导演/视觉手册 + 角色记忆 + 模型地图
- [x] 生产监督报告（完备性检查）
- [x] 多版视频选用 + 入点/出点
- [x] Comfy 模型诊断 + 错误码中文
- [x] Skill 在线编辑保存
- [x] 项目 JSON 备份
- [x] 定稿视频 MiniMax H3 图生视频（Comfy）
- [x] 合规红黄线
- [x] 软件授权加密（卡密 / 机器绑定）
- [x] 原创画风技能包（`xg_*`）
- [ ] 向量长期记忆（后续）
- [ ] 剪映级多轨精细剪辑（后续）
- [ ] 一键安装包 Inno Setup（见 packaging.md）

---

## 小白 3 步上手

1. **设置**：配置语言模型 API Key（DeepSeek/通义），点测试 —— **只负责写剧本和提示词**  
2. **启动本机 ComfyUI**（默认 `http://127.0.0.1:8188`），在「算力」页确认在线 —— **出图、出视频**  
3. **一键出片**：粘贴剧情 → 等待成片  

安装 **ffmpeg** 后才能导出合镜 MP4。

---

## 注意事项

- **不要混淆**：云 API Key ≠ 画图；画图/视频一律 ComfyUI。  
- **ComfyUI**：本机 :8188；设置页可「模型检测」。  
- **合规词库**：真实词库云端下发；仓库 `dict/` 仅为占位。  
- **口型**：默认 H3 为图生视频；LTX 工作流才可能音频驱动。  
- **画风 / Agent 技能**：西瓜原创 `xg_*`。  
- **版权**：根目录 `LICENSE` 与 `NOTICE`。  

---

*西瓜品牌 · 专业 AI 短剧创作工具*
