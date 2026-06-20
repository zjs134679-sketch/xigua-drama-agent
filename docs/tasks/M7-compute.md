# M7 任务：国内远程主机 + 云 API 接入（算力层完善）

## 工作目录
仓库根：`F:\waterlmelon\xigua-drama-agent`（沙箱可写）。Python 用 `C:/xgvenv/Scripts/python.exe`。

## ⚠️ 执行约束
- **你是后台 detached、无法提问：遇取舍自行用合理默认、绝不停下等人、绝不退回任务。**
- **只写实现代码/脚本/单测；不要运行 npm install / build / vite / dev / tauri / nuitka / git commit 或任何长命令。** 完成后只列改动文件清单，验证与提交人工接手。

## 现状（勿重造）
- 算力抽象 `backend/app/services/compute/`：`base.py`（`ComputeNode` ABC：`health()`/`text2image(ImageJob)->JobResult`；`ImageJob` 含 `reference_images: list[str]`）、`local_comfy.py`（/prompt→轮询/history→/view 下载；`_inject` 把 `job.reference_images` 按顺序塞进 LoadImage 节点的 `inputs.image`，**但没上传到 ComfyUI /upload/image**）、`remote_comfy.py`（继承 local，仅加 `Authorization: Bearer <token>`）、`registry.py`（`build_node(row)` 现支持 local/remote；`get_active_node(db)` 取 is_active 最高 priority）。
- 节点表 `models/system.py::ComputeNode`：字段 `name/type/base_url/token/priority/is_active/capabilities/last_status/extra`（**无 provider/api_key/model**）。
- 节点 API 已有：`api/compute.py` 的 `GET/POST /compute/nodes`、`PUT/DELETE /compute/nodes/{id}`、`POST /compute/nodes/{id}/test`、`GET /compute/health`、`POST /compute/text2image`。建表用 `Base.metadata.create_all`（无 Alembic，加列直接改模型即可，开发库会重建）。
- 合规在**服务层**：`api/compute.py`/`asset_generation.py` 调 node 前已 `check()`，故云/远程节点**自动受合规约束**，无需在节点层再做。
- 前端：活动栏已有「算力」项（`App.tsx` 的 RAIL，`Server` 图标，当前无对应页）；`api/client.ts`、深色主题、`features/` 既有页可参考。

## 交付内容

### 1. 参考图上传（修 Kontext/图生视频，本地+远程都要）
- 在 `local_comfy`（被 remote 继承）：注入 LoadImage 文件名前，先把每个 `reference_images` 项（本地路径或 URL）以 multipart `POST {base_url}/upload/image`（带 `self._headers()`）上传，用返回的 `name` 作为 LoadImage 的 `inputs.image`。本地与远程同一套（远程即走网络+鉴权头）。失败要给清晰错误。

### 2. 云 API 适配器（cloud_api）
- 新增 `services/compute/cloud_api.py`：`CloudApiNode(ComputeNode)`，按 `provider` 分发；先实现两个国内 provider（**结构可扩展**，buildRequest/parse/poll/extract 风格）：
  - `wan`（阿里通义万相 / DashScope）：文生图（多为异步：提交任务→轮询→取图 URL→下载落 `data/oss`）。
  - `seedance`（火山/字节）：文生图或图生视频（按其 REST 约定）。
  - 用 httpx；`base_url`/`model` 可由节点配置覆盖；**确切 endpoint/参数若无把握就采用各家公开 REST 约定的合理默认，并在 docs 注明"需真实 key 联调校验"**。
- `registry.build_node`：新增 `cloud_api` → `CloudApiNode(provider, base_url, api_key, model, token)`。
- `models/system.py::ComputeNode`：**加列** `provider: str|None`、`api_key: str|None`、`model: str|None`。

### 3. 节点管理前端页
- 新增 `frontend/src/features/ComputeNodesView.tsx`：列出节点（名/类型/base_url/优先级/active/last_status），新增节点表单——类型 `local_comfy`（base_url）、`remote_comfy`（base_url+token）、`cloud_api`（provider+base_url+api_key+model）；按钮：测试连通(`POST /compute/nodes/{id}/test`)、启用/停用、设优先级、删除。
- `App.tsx`：活动栏「算力」项切到此页（参考现有 view 切换）。
- `api/client.ts`：增节点 CRUD + test 方法。
- `api/compute.py`：节点序列化补 provider/model 字段（**api_key 不回传明文**，用是否已配置的布尔/掩码表示）。

### 4. 文档
- `docs/compute-nodes.md`：如何加 **智星云/AutoDL/恒源云/矩池云/趋动云/PPIO** 远程节点（公网端口映射 + token）；如何加 **WAN/Seedance** 云 key；本地优先、远程/云作溢出的优先级建议。

### 5. 测试（占位/可 mock）
- cloud_api：请求构造（mock httpx）、provider 分发正确。
- registry：cloud_api/remote/local 各建对应类型。
- 参考图上传：构造 multipart（mock）。
- 不破坏现有 19 个测试。

## 硬约束
- **任何代码/测试/注释/词库都不得出现真实敏感词。**
- api_key 不明文回传前端；网络失败一律清晰错误/兜底不崩。
- 复用现有 `ComputeNode`/`registry`/`get_active_node`/节点 API/BanScreen/深色主题，不破坏现有接口与 19 测试。

## 完成判据（人工验收）
- `cd backend && C:/xgvenv/Scripts/python.exe -m pytest -q` 全绿（19 + 新增）。
- `cd frontend && npm run build` 通过。
- 加一个 remote_comfy 节点并 `test` 能反映连通；cloud_api 节点结构就绪（真实 key 由人工联调）。
