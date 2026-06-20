# 算力节点配置

算力页支持三种节点：本地 ComfyUI、远程 ComfyUI 和云 API。节点按优先级从高到低选择，只有“启用”的节点参与选择。

## 推荐优先级

建议把本地节点设为最高优先级，例如 `300`；远程主机设为 `200`；按量计费的云 API 设为 `100`。当前调度器选择最高优先级的启用节点，不会在一次失败后自动切换到下一节点，因此节点离线时应在算力页测试并停用，再启用备用节点。

## 本地 ComfyUI

1. 在 ComfyUI 启动参数中监听本机接口，默认地址为 `http://127.0.0.1:8188`。
2. 在算力页新增“本地 ComfyUI”，填写地址并设为最高优先级。
3. 点击“测试连接”，状态应变为在线。

参考图生成时，后端会先把每张本地文件或 HTTP(S) 图片上传到该节点的 `/upload/image`，再把节点返回的文件名写入工作流的 `LoadImage`。

## 国内远程 ComfyUI

智星云、AutoDL、恒源云、矩池云、趋动云和 PPIO 的接入方式相同：在实例内运行 ComfyUI，把 `8188` 映射到平台提供的公网端口，再把公网 HTTPS 地址配置成远程节点。各平台控制台名称可能是“端口映射”“自定义服务”或“公网访问”。

| 平台 | 实例侧端口 | 算力页 Base URL |
| --- | --- | --- |
| 智星云 | `8188` | 控制台生成的公网映射地址 |
| AutoDL | `8188` | 自定义服务生成的公网地址 |
| 恒源云 | `8188` | 实例端口映射生成的公网地址 |
| 矩池云 | `8188` | 实例开放端口对应的公网地址 |
| 趋动云 | `8188` | 在线服务或端口映射地址 |
| PPIO | `8188` | 实例服务映射生成的公网地址 |

配置步骤：

1. ComfyUI 监听实例内可被代理访问的地址和 `8188` 端口。
2. 在平台控制台建立公网 HTTPS 到实例 `8188` 的映射，不要在 Base URL 末尾填写 `/prompt`。
3. 在网关或反向代理中配置访问 token；算力页选择“远程 ComfyUI”并填写同一个 token。
4. 后端请求会携带 `Authorization: Bearer <token>`，包括 `/system_stats`、`/upload/image`、`/prompt`、`/history` 和 `/view`。
5. 点击“测试连接”。失败时检查实例状态、映射端口、证书、网关鉴权和安全组。

不要把无鉴权的 ComfyUI 端口直接暴露到公网。平台分配的映射域名或端口可能在实例重启后变化，变化后需同步更新节点地址。

## WAN 云 API

WAN 节点使用 DashScope 异步文生图约定，默认配置为：

- Provider：`wan`
- Base URL：`https://dashscope.aliyuncs.com`
- Model：`wanx-v1`
- 提交：`POST /api/v1/services/aigc/text2image/image-synthesis`
- 查询：`GET /api/v1/tasks/{task_id}`

在阿里云控制台创建 DashScope API key，在算力页新增“云 API”，选择 WAN 后填写 key。Base URL 和模型均可覆盖。后端只返回“是否已配置”，不会把 key 明文返回前端。

## Seedance 云 API

Seedance 节点使用火山方舟异步视频任务约定，默认配置为：

- Provider：`seedance`
- Base URL：`https://ark.cn-beijing.volces.com`
- Model：`doubao-seedance-1-0-pro-250528`
- 提交：`POST /api/v3/contents/generations/tasks`
- 查询：`GET /api/v3/contents/generations/tasks/{task_id}`

在火山方舟控制台创建 API key，并确认账号已开通对应模型。图生视频的参考图需要是云服务可访问的 HTTP(S) URL，本地文件不会直接发送给云服务。生成结果会下载到后端 `data/oss`，结果元数据中的 `media_type` 为 `video`。

云厂商会调整模型版本、账号权限和请求字段。上述默认值按公开 REST 约定实现，部署前必须用真实 key 联调校验；如控制台给出不同的模型 ID 或网关地址，以控制台为准并在节点配置中覆盖。真实 key 不要写入仓库、日志或截图。

## 故障检查

- “鉴权失败”：重新配置 token 或 API key，确认 key 对应正确服务和地域。
- “服务异常”：查看云厂商或远程实例状态，稍后重试。
- “连接失败”：检查 DNS、HTTPS 证书、端口映射、安全组和本机代理。
- “参考图上传失败”：确认文件存在、图片 URL 可访问，且远程网关允许 multipart 请求到 `/upload/image`。
- “任务成功但未返回地址”：核对模型版本与当前厂商响应格式，并按真实 key 联调结果调整 provider 适配器。
