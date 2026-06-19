# 西瓜短剧Agent（国内版）

> 面向中国合规市场的 AI 短剧生产软件，对标 Toonflow 的全流程但全新 Python 实现，深色专业 UI（Cursor + 剪映风），西瓜品牌。

---

## 技术栈

| 层次 | 技术 |
|------|------|
| 后端主服务 | Python FastAPI（端口 5678） |
| 前端 | React + Vite + TypeScript（端口 5173） |
| 桌面壳 | Tauri（需 Rust 环境） |
| 云端认证 | FastAPI（端口 8100） |
| 算力抽象 | ComputeNode — 支持本地 / 国内远程 / 云三类算力 |
| 合规过滤 | 内置中国合规红黄线敏感词过滤：红线硬拦截、黄线轻提示、三次红线封号 |

---

## 目录结构

```
西瓜短剧Agent 国内版/
├── backend/        # Python FastAPI 主后端，入口 app.main:app
├── frontend/       # React + Vite + TS 前端
├── src-tauri/      # Tauri 桌面壳配置与 Rust 代码
├── auth-server/    # 云端授权认证服务，入口 app.main:app
└── scripts/        # 运维脚本（启动.bat 等）
```

---

## 本地运行

### 方式一：一键启动（推荐）

双击 `scripts\启动.bat`，脚本会自动：
- 检测并创建 Python 虚拟环境（backend/.venv）
- 安装后端依赖
- 检测并安装前端 npm 依赖
- 分别在新窗口中启动后端与前端

启动后访问：
- 后端 API 文档：http://127.0.0.1:5678/docs
- 前端界面：http://localhost:5173

### 方式二：手动启动

```bash
# 后端（在 backend/ 目录）
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 5678

# 前端（在 frontend/ 目录）
npm run dev

# auth-server（在 auth-server/ 目录，按需启动）
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8100
```

---

## 注意事项

- **Tauri 桌面构建**：需先安装 [Rust](https://rustup.rs/)，然后在项目根目录执行 `npx tauri dev`。
- **合规词库**：真实敏感词库由云端加密下发，仓库内 `backend/dict/` 只放占位演示词，运行时优先加载 `dict/*.local.txt`（已 gitignore）。
- **算力切换**：在前端「设置」页面可切换本地 ComfyUI / 国内远程 / 云端三种算力模式。

---

## 当前进度

- [x] M0：项目脚手架搭建（FastAPI + React + Tauri 三层架构）
- [x] M0+：合规红黄线系统（敏感词过滤、三次封号逻辑）
- [x] M1：本地 ComfyUI 文生图闭环（API 工作流注入 + 合规过滤已验证；真实出图需本机开启 ComfyUI :8188）
- [ ] M2：分镜脚本生成（待开发）
- [ ] M3：视频合成（ComfyUI 视频适配器）
- [ ] M4：完整短剧生产流水线

---

*西瓜品牌 · 专业 AI 短剧创作工具*
