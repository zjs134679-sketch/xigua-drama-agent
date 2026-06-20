# M6 任务：成品化（账号登录接入 + 赞助弹窗 + 升级提醒 + Nuitka 打包脚本）

> Inno Setup 安装包**本次不做**（延后到功能调试完成后）。本任务只到"能登录、能查更新、有可用的 Nuitka 编译脚本"为止。

## 工作目录
仓库根：`F:\waterlmelon\xigua-drama-agent`（沙箱可写）。Python 用 `C:/xgvenv/Scripts/python.exe`。

## ⚠️ 执行约束（重要）
- **Codex 是后台 detached 运行、无法向用户提问**：遇到任何取舍**自行采用合理默认、绝不停下等人/不把任务退回**。
- **只写实现代码、脚本与单测**。**不要**运行 `npm install` / `npm run build` / vite / dev / tauri / **nuitka** / `git commit`，**不要**跑任何长时间或交互式命令。完成后只列改动文件清单，验证与提交由人工接手。

## 现状（已存在，勿重造）
- 云端认证 `auth-server/app/main.py`：`POST /auth/register`、`POST /auth/login`（返回 `{token, user}`，user 含 `plan/role/violation_count/banned/banned_reason`；已封禁登录返回 403）、`GET /auth/me`（Bearer）、`GET /version`（`{latest,url,notes}`）、`/compliance/*`。**无 CORS 中间件**。
- 前端 `frontend/src/`：`App.tsx`（直接进工作台，**无登录**）、`api/client.ts`（fetch `/api` 反代到后端 5678）、`components/BanScreen.tsx`（已有封禁页）、深色主题 `theme/theme.css`。
- 后端 `backend/app/`：`settings.auth_server_url`（默认 http://127.0.0.1:8100）、`core/config.py`、`main.py`。
- 产品定调（务必遵守）：登录注册**简化版**——注册+登录+发 token，能进软件即可，不做授权校验/宽限期；**赞助 = 登录后弹一个提示**（文字 + 收款码/链接占位），**不强制、可关闭、关掉就能用**；收费钩子（plan/expire_at）已在 users 表预留，本次不接支付。

## 交付内容

### 1. 账号登录接入客户端
- **auth-server**：加 CORS 中间件（允许前端 5173 / tauri 来源），便于前端直连。
- **前端**：
  - 新增 `features/AuthView.tsx`（登录/注册切换，调 auth-server `/auth/register`、`/auth/login`）。
  - `App.tsx`：未登录先显示 AuthView；登录成功存 token（localStorage）与 user，进入工作台。
  - `api/client.ts`：新增 auth 客户端（auth-server 基址用 `import.meta.env.VITE_AUTH_URL || "http://127.0.0.1:8100"`），登录后把 `Authorization: Bearer <token>` 用于需要鉴权的请求；提供 `logout()`。
  - 若 user.banned 或登录返回 403 banned → 显示现有 `BanScreen`。
  - 顶栏头像处显示当前用户名 + 退出登录。
- **赞助弹窗** `features/SponsorDialog.tsx`：登录成功后弹一次（本次会话），文案"喜欢就请作者吃瓜🍉"+ 收款码图片占位 + 关闭按钮；关闭即正常使用；可加"本次不再提示"。

### 2. 升级提醒
- 客户端启动后查 auth-server `GET /version`，与本地版本（`backend` settings.version / 前端常量，自定一处常量即可）比对；`latest` 更高 → 顶部显示可关闭的更新条（文案 + `url` 下载链接，url 为空则只提示"有新版本"）。失败静默。

### 3. Nuitka 打包脚本（只写不跑）
- 新增后端冻结入口 `backend/server_entry.py`：用 `uvicorn.run(app, host, port)` 直接起（避免 `app.main:app` 字符串在冻结 exe 里导入困难）。
- 新增 `scripts/build_backend.bat`（Windows）：用 Nuitka 把 `server_entry.py` 编成单文件 exe，包含必要 `--include-package`（fastapi, uvicorn, pydantic, sqlalchemy, httpx, pypinyin 等）、`--include-data-dir` 带上 `app/workflows` 与 `dict`、`--standalone --onefile --assume-yes-for-downloads --output-dir=dist`。脚本顶部注释写清前置（`pip install nuitka`）。**不要运行它。**
- 新增 `docs/packaging.md`：Nuitka 打包步骤 + 运行产物方式；并写明 **Inno Setup 安装包章节为 TODO（功能调试完成后再做）**、SQLCipher DB 加密为后续项。

### 4. 测试（`backend/tests/`，占位词/可 mock）
- auth-server CORS 头存在；`/version` 返回结构正确。
- 不破坏现有 17 个测试。前端无需新增测试（但 `npm run build` 要能过——由人工验证）。

## 硬约束
- **任何代码/测试/注释/词库都不得出现真实敏感词。**
- 不实际运行 nuitka / 安装包 / 长命令；登录、查版本失败都要静默兜底不崩。
- 复用现有 BanScreen、深色主题、client 模式、settings；不破坏现有接口与 17 个测试。

## 完成判据（人工验收）
- `cd backend && C:/xgvenv/Scripts/python.exe -m pytest -q` 全绿（17 + 新增）。
- `cd frontend && npm run build` 通过。
- 起 auth-server + backend + 前端：未登录显示登录页；注册/登录后进工作台并弹一次赞助；有更新时显示更新条；封禁用户显示 BanScreen。
- `scripts/build_backend.bat` 内容正确（人工择机实跑 Nuitka）。
