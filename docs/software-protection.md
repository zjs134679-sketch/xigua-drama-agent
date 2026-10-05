# 软件加密与授权（西瓜短剧Agent）

本文说明如何用「代码打包 + 云端卡密授权 + 机器绑定」保护客户端，而不是指望单一加壳。

> **加固版（industrial-v1）** 细节见 [security-hardening.md](./security-hardening.md)：  
> 访问 JWT 含过期与机器绑定、短期能力票、PBKDF2 60 万次、本地门禁先验签。  
> **不存在**面向最终用户的「军事级不可破解」客户端加密；真正控权在云端。

---

## 1. 防护分层

| 层 | 手段 | 作用 |
|----|------|------|
| 代码保护 | Nuitka 编译后端 exe、前端 minify、Tauri 安装包 | 用户拿不到 Python 源码 |
| 账号登录 | auth-server JWT | 身份识别、封号 |
| **授权加密** | 卡密激活 + plan/到期 + 机器码 | 没买/过期不能用核心功能 |
| 敏感资产 | 合规词库云端下发、API Key 不入库明文回传 | 防泄露 |
| 本地库 | SQLCipher（后续） | 防直接拷库 |

**原则**：真正可控的是云端授权；本地程序只能提高逆向成本。

---

## 2. 授权模型（已实现）

### 用户字段
- `plan`：`trial` / `pro` / `studio` / `free`
- `expire_at`：到期时间（UTC）
- `machines`：已绑定设备码列表（JSON）
- `max_machines`：默认 2 台

### 判定
```
license_active =
  未封禁
  AND expire_at 存在
  AND expire_at >= 现在
```

环境变量 `XIGUA_LICENSE_REQUIRED=0` 时可关闭强制授权（纯开发）。

### 试用
- 注册自动 `plan=trial`，`expire_at = now + XIGUA_TRIAL_DAYS`（默认 7 天）

### 卡密
- 格式示例：`XG-A1B2-C3D4-E5F6-G7H8`
- 激活后：叠加有效期、升级 plan、绑定当前机器

---

## 3. 运维操作

### 启动 auth-server（生产）

> 授权服务已从软件工程目录**迁出**到独立卡密平台：  
> `E:\xigua Agent  密码管理\auth-server`  
> 推荐直接双击：`E:\xigua Agent  密码管理\一键启动.bat`

```powershell
$env:XIGUA_AUTH_SECRET = "请换成长随机串"
$env:XIGUA_ADMIN_SECRET = "管理员发卡密钥"
$env:XIGUA_TRIAL_DAYS = "7"
$env:XIGUA_LICENSE_REQUIRED = "1"
$env:XIGUA_MAX_MACHINES = "2"
cd "E:\xigua Agent  密码管理\auth-server"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8100
```

### 批量发卡

```powershell
$env:XIGUA_ADMIN_SECRET = "管理员发卡密钥"
python scripts\gen_licenses.py --count 10 --plan pro --days 365 --out licenses.txt
```

或 HTTP：

```http
POST /admin/licenses
X-Admin-Secret: <密钥>
{"count":10,"plan":"pro","days":365,"max_machines":2}
```

### 手动给用户延期

```http
POST /admin/grant
X-Admin-Secret: <密钥>
{"username":"客户名","plan":"pro","days":365}
```

### 换机解绑

```http
POST /admin/unbind-machines
X-Admin-Secret: <密钥>
{"username":"客户名"}
```

---

## 4. 客户端行为

1. 登录 / 注册时上传 `machine_id`（本机持久 UUID）
2. `license_active=false` → 强制进入 **软件授权激活** 页
3. 顶栏显示 `plan · 到期日`，可点开续费
4. 每 6 小时 `POST /auth/heartbeat` 校验

---

## 5. 本地后端强制校验（防绕过 UI）

开发默认关闭，避免打断本地调试：

```powershell
# 正式包 / 发给客户时打开
$env:XIGUA_LICENSE_ENFORCE = "true"
$env:XIGUA_AUTH_SERVER_URL = "https://你的授权域名"
```

开启后，出图 / 出片 / 分镜生成等接口会带 JWT 向 auth-server 校验 `license_active`。

配置项：`backend` 环境变量前缀 `XIGUA_`，字段 `license_enforce`。

---

## 6. 代码打包（源码加密）

### 后端 Nuitka

见 `docs/packaging.md` 与 `scripts/build_backend.bat`：

```text
backend\dist\xigua-backend.exe
```

### 前端

```powershell
cd frontend
npm run build
```

### 桌面壳

装好 Rust 后 `tauri build`，将 backend exe 作为 sidecar（见 `src-tauri/README.md`）。

### 发布检查清单

- [ ] 改掉 `XIGUA_AUTH_SECRET` / `XIGUA_ADMIN_SECRET`
- [ ] auth-server 公网 HTTPS
- [ ] 客户端 `VITE_AUTH_URL` / `XIGUA_AUTH_SERVER_URL` 指向生产
- [ ] `XIGUA_LICENSE_REQUIRED=1`
- [ ] `XIGUA_LICENSE_ENFORCE=true`（主后端）
- [ ] Nuitka 出包，不发源码仓库
- [ ] 不把 `auth.db`、真实词库、管理员密钥打进安装包

---

## 7. 接口一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/auth/register` | 注册（可带 machine_id） |
| POST | `/auth/login` | 登录 |
| GET | `/auth/me` | 用户+授权状态（可带 X-Machine-Id） |
| POST | `/auth/heartbeat` | 心跳校验 |
| POST | `/license/activate` | 卡密激活 |
| POST | `/admin/licenses` | 发卡 |
| POST | `/admin/grant` | 直接延期 |
| POST | `/admin/unbind-machines` | 清机器绑定 |

`user` 返回字段增加：`expire_at`、`license_active`、`license_required`、`license_reason`、`machines_bound`、`max_machines`。

---

## 8. 局限（务必知晓）

- 纯离线破解、改本地二进制仍可能绕过，需持续更新与服务端能力下沉
- 浏览器端 machine_id 是持久 UUID，不是硬件指纹；Tauri 版可再换成 OS 机器码
- 不要在客户端硬编码管理员密钥

*西瓜短剧Agent · 授权加密说明*
