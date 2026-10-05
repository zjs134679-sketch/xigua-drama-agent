# 授权服务器地址：本机 vs 卖给客户

## 流程（正确理解）

```
用户打开短剧软件
    │
    ├─① 先连本机短剧后端 :5678
    │     GET /api/setup/public-config
    │     → 返回 auth_server_url（来自 config.env 的 XIGUA_AUTH_SERVER_URL）
    │
    └─② 再连授权服（登录/注册/卡密/心跳）
          本机开发：http://127.0.0.1:8100
          卖给客户：https://你的公网域名 或 http://公网IP:8100
```

| 角色 | 跑在哪 | 端口 | 谁访问 |
|------|--------|------|--------|
| **auth-server**（发卡、账号、卡密） | **你的服务器**（卖客户时）/ 本机（自测） | **8100** | 所有用户客户端 + 你的发卡后台 |
| **短剧主程序**（出图/分镜） | **用户电脑** | **5678** | 仅本机浏览器 |

卖客户时：**auth-server 只装你这边一份（公网可访问）**；用户电脑只装短剧安装包，**不要**把 auth-server 和 `auth.db` 打进用户安装包。

---

## 本机自测

1. 启动密码管理目录里的 auth-server（8100）
2. 启动短剧后端（5678）
3. `config.env` / 环境变量保持：

```env
XIGUA_AUTH_SERVER_URL=http://127.0.0.1:8100
XIGUA_AUTH_SECRET=与auth-server相同的密钥
XIGUA_LICENSE_ENFORCE=true
```

---

## 卖给客户（公网授权）

### 1）你的服务器上部署 auth-server

- 监听 `0.0.0.0:8100`（或反代到 HTTPS 443）
- 防火墙/安全组放行端口
- 设置强密钥：

```powershell
$env:XIGUA_AUTH_SECRET = "长随机串"
$env:XIGUA_ADMIN_SECRET = "另一长随机串"
$env:XIGUA_LICENSE_REQUIRED = "1"
```

- 建议前面加 Nginx/Caddy 做 **HTTPS**，例如 `https://auth.你的域名.com`

### 2）用户安装目录 `config.env`

```env
XIGUA_LICENSE_ENFORCE=true
XIGUA_AUTH_SERVER_URL=https://auth.你的域名.com
XIGUA_AUTH_SECRET=与服务器上相同的长随机串
```

> `XIGUA_AUTH_SECRET` 用于本机后端验「能力票」，**必须与 auth-server 一致**。  
> 不要把 `XIGUA_ADMIN_SECRET` 写进用户包。

### 3）用户打开软件后

1. 启动器打开 `http://127.0.0.1:5678/`
2. 前端请求 `/api/setup/public-config` → 得到你的公网授权地址
3. 登录/卡密请求打到 **你的公网 8100**，而不是用户自己的 127.0.0.1

---

## 改地址要改哪里

| 位置 | 变量 | 作用 |
|------|------|------|
| 用户安装目录 `config.env` | `XIGUA_AUTH_SERVER_URL` | 主后端门禁 + 下发给前端 |
| auth-server 本机/云主机 | 监听地址与域名 | 真正提供登录卡密 |
| 前端构建 `VITE_AUTH_URL` | 可选默认 | 仅后端未起时的兜底；正常以 public-config 为准 |

**不必**为每个客户单独编译前端；改 `config.env` 即可切换授权服。

---

## CORS

auth-server 已允许 `localhost` / `tauri` 来源。若用户从 `http://127.0.0.1:5678` 访问公网授权服，需保证 auth-server CORS 允许该来源（当前正则已含 localhost）。公网部署若有额外域名，在 auth-server `CORSMiddleware` 中放行。
