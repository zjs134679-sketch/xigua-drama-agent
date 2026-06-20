# M5 任务：合规全链路完善

## 工作目录
仓库根：`F:\waterlmelon\xigua-drama-agent`（沙箱可写）。Python 用 `C:/xgvenv/Scripts/python.exe`。

## ⚠️ 执行约束（重要，避免卡死）
- **只写实现代码与单测文件**。**不要**运行 `npm install` / `npm run build` / vite / dev server / tauri / `git commit`，**不要**跑任何长时间或交互式命令（会卡死沙箱）。完成后只列改动文件清单，验证与提交由人工接手。允许快速只读自检（`python -c "import app.main"`）。

## 现状（已存在，勿重造）
- 合规核心 `backend/app/services/compliance/`：`check(text)->FilterResult`（红线 `blocked`、`warn`）、`enforce.record_violation(db, username, result, source)`（掩码留痕、本地累计封号）、`dictionary`（加载 `dict/red.txt|yellow.txt`，存在 `dict/*.local.txt` 时优先）。
- 出图路径已接合规：`api/compute.py`、`api/assets.py`、`api/timeline.py`（字幕）——红线 451 + `record_violation`、黄线 warn。**这是要对齐的范式。**
- 文本路径**只查了输入、且没留痕**：`api/script.py`（`check(ep.content)` / `check(content)` 后生成，红线 451 但**无 record_violation**、**未查 LLM 输出**）、`api/extract.py`（同样只查 `content`）。LLM 产出在 `services/agents/script_agent.py`、`extract_agent.py`，LLM 客户端 `services/llm/client.py`。
- 云端认证 `auth-server/app/main.py`：已有 `POST /compliance/violation`（服务端权威累计+封号）、users 表。**尚无** `/compliance/dict`。
- 客户端配置 `settings.auth_server_url` 已存在。

## 交付内容

### 1. 文本路径补全合规（对齐出图路径范式）
- `api/script.py`、`api/extract.py`：除现有**输入**检查外，**对 LLM 输出也过 `check()`**（生成的剧本/抽取文本）；**所有红线（输入或输出）都调 `enforce.record_violation(db, username, result, source)`**（source 用 `"script_input"`/`"script_output"`/`"extract_input"`/`"extract_output"`），返回 451 含 `violation_count`/`banned`（对齐 compute）；黄线返回 `warn=true` + hits。
- 请求体支持可选 `username`（与 compute 一致，用于封号计数）。封号用户调用这些接口前应被拦（参考 `api/assets.py` 的 `_ensure_active_user`，可抽公共依赖复用）。

### 2. 云端词库下发对接
- 新增 auth-server 端点 `GET /compliance/dict?since=<version>`：返回当前红/黄线词库 + `version`，做**轻量混淆**（stdlib：gzip+base64，可选对称密钥；M6 再硬化，勿引重依赖）。**auth-server 仓库内的种子词库同样只放占位/演示词，严禁真实敏感词。**
- 新增客户端 `backend/app/services/compliance/sync.py`：用 httpx 拉 `auth_server_url + /compliance/dict`，解混淆后写 `dict/red.local.txt`/`dict/yellow.local.txt`（gitignore），记录本地 version，调 `dictionary.reload()`。auth-server 不可达时静默跳过（用本地占位库），不崩。
- `api/compliance.py` 增 `POST /compliance/sync`（手动触发）；`main.py` 启动时**尽力**同步一次（失败不影响启动）。

### 3. 封号服务端权威化
- `enforce` 增 `report_to_auth(username, result)`：红线时**尽力**上报 auth-server `POST /compliance/violation`（httpx，失败不阻断本地流程）。`record_violation` 在红线分支调用它（保持现有本地累计为兜底）。
- 增客户端拉取封号态的途径（如登录/启动时查 auth-server，命中 banned 则本地 User.banned 置位；前端据此显示已有的 BanScreen）。

### 4. 前端
- 在剧本/小说编辑视图把文本路径的 **红线 451（硬拦截提示）/ 黄线 warn（高亮+建议）** 呈现出来（复用现有深色主题与 `client.ts` 模式）。
- `client.ts` 增 `syncCompliance()`（调 `/compliance/sync`）；可在设置或状态条加一个"词库已同步 vX"指示（可选、简单即可）。

### 5. 测试（`backend/tests/`，**占位词**）
- 文本路径：LLM 输出含占位红线 → 451 + 记一次 violation；黄线 → warn。
- `sync.py`：mock auth-server 返回占位词库 → 写出 `.local` 并 `dictionary.reload()` 后能命中新占位词。
- 不破坏现有 12 个测试。

## 硬约束
- **任何代码/测试/注释/词库（含 auth-server 种子）都不得出现真实敏感词。**
- 文本红线一律 `record_violation` 掩码留痕；auth-server 上报与词库同步**失败都要静默兜底、不崩**。
- 复用现有 `check`/`enforce`/`dictionary`、`get_db`、`settings`，不破坏现有接口与测试。

## 完成判据（人工验收）
- `cd backend && C:/xgvenv/Scripts/python.exe -m pytest -q` 全绿。
- `cd frontend && npm run build` 通过。
- 起 auth-server 时 `POST /compliance/sync` 能拉词库写 `.local` 并生效；脱机时用本地占位库不崩。
