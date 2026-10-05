# 授权加密加固说明（industrial-v1）

> **重要**：不存在可对最终用户客户端实现的「军事级不可破解 DRM」。  
> 本文档描述的是**工业级加固**：提高逆向与伪造成本，**最终裁决权在云端 auth-server**。

---

## 1. 本轮加固内容

| 模块 | 措施 |
|------|------|
| 密码 | PBKDF2-HMAC-SHA256，默认 **600,000** 次迭代；兼容旧 200k 哈希 |
| 访问 JWT | 含 `exp` / `jti` / `typ=access` / 可选 `mid` 机器绑定；过期需重新登录 |
| 能力票 | 短期 JWT `typ=capability`（默认 30 分钟），绑定 plan/lic/mid |
| 本地后端 | `X-Capability` 先验签放行，失败再回源 `/auth/me` |
| 设备码 | 前端 `xg2-<uuid>` + 环境指纹旁路记录 |
| 日志 | 卡密失败写 HMAC digest，避免明文卡密进日志 |
| 配置 | 默认 SECRET 启动时 warnings |

---

## 2. 生产环境必做

```powershell
# 授权服务与主后端使用【同一】长随机密钥
$env:XIGUA_AUTH_SECRET = "<至少 32 字节随机>"
$env:XIGUA_ADMIN_SECRET = "<另一长随机>"
$env:XIGUA_LICENSE_REQUIRED = "1"
$env:XIGUA_LICENSE_ENFORCE = "true"   # 主后端
$env:XIGUA_AUTH_SERVER_URL = "https://你的授权域名"
# 可选
$env:XIGUA_JWT_TTL_SECONDS = "604800"   # 7 天
$env:XIGUA_CAP_TTL_SECONDS = "1800"     # 30 分钟
$env:XIGUA_PBKDF2_ITERS = "600000"
```

- auth-server 与 drama 后端的 `XIGUA_AUTH_SECRET` **必须一致**，否则能力票验签失败（仍可回源 `/auth/me`）。
- 使用 **HTTPS**；不要把 `auth.db`、管理员密钥打进客户端安装包。
- 发布客户端用 **Nuitka / minify / Tauri**（见 packaging.md）。

---

## 3. 安全边界（务必知晓）

| 能做到 | 做不到 |
|--------|--------|
| 无有效授权难用云端/门禁 API | 阻止精通逆向的人改本地二进制 |
| 令牌过期、机器不一致拒绝 | 浏览器清 localStorage 后「新机器」 |
| 能力票短时效降低重放窗口 | 完全离线永久授权且无法伪造 |
| 密码抗离线撞库（高迭代） | 用户弱密码本身 |

---

## 4. 相关代码

| 路径 | 说明 |
|------|------|
| `E:\xigua Agent  密码管理\auth-server\app\main.py` | 发卡/JWT/能力票 |
| `backend/app/services/license_gate.py` | 本地门禁 |
| `backend/app/services/license_crypto.py` | 能力票验签 |
| `frontend/src/api/client.ts` | 设备码、能力票头、会话 |

*industrial-v1 · 西瓜短剧 Agent*
