# 后端打包

## Nuitka 单文件构建

前置环境为 Python 3.11、项目后端依赖和 Nuitka。使用项目约定的虚拟环境时，可先安装 Nuitka：

```powershell
C:\xgvenv\Scripts\python.exe -m pip install nuitka
```

在仓库根目录运行：

```powershell
scripts\build_backend.bat
```

脚本以 `backend/server_entry.py` 为冻结入口，将 FastAPI、Uvicorn 和后端动态依赖一并收集，并携带 `app/workflows` 与 `dict` 数据目录。构建产物位于：

```text
backend\dist\xigua-backend.exe
```

运行产物后，本地服务监听 `http://127.0.0.1:5678`。运行前可通过 `XIGUA_` 前缀环境变量覆盖后端配置，例如认证服务地址：

```powershell
$env:XIGUA_AUTH_SERVER_URL = "https://example.invalid"
backend\dist\xigua-backend.exe
```

## 一键发布（推荐）

在仓库根目录（PowerShell）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

流程：

1. `frontend` Vite production 构建 → `release\web`
2. 复制 `data\skills`、dict、workflows
3. **Nuitka** 编译 `xigua-backend.exe`（源码保护）
4. 写入启动器 / `config.env.example`（默认 `XIGUA_LICENSE_ENFORCE=true`）
5. **Inno Setup 6** 编译 → `release\西瓜短剧Agent-Setup-0.1.1.exe`（默认装到用户目录，强制桌面图标）

前置：Node.js、backend `.venv` 依赖、Inno Setup 6（`C:\Program Files (x86)\Inno Setup 6\ISCC.exe`）。

脚本：`scripts\build_release.ps1`  
安装脚本：`installer\xigua-setup.iss`

### 安装后

1. 先启动 **auth-server**（卡密授权，端口 8100）
2. 编辑安装目录 `config.env`：填写 `XIGUA_AUTH_SECRET`（与授权服一致）与 `XIGUA_AUTH_SERVER_URL`
3. 双击 **启动西瓜短剧.bat** → 浏览器 `http://127.0.0.1:5678/`
4. 注册 / 登录 / 卡密激活

### 仅后端 Nuitka

```powershell
scripts\build_backend.bat
```


## 后续安全项

SQLCipher 数据库加密留作后续版本实现；当前仍使用普通 SQLite，不应将本地数据库视为加密存储。

## 软件授权加密

卡密 / 机器绑定 / 强制授权见 **[software-protection.md](./software-protection.md)**。

正式包建议同时设置：

```powershell
$env:XIGUA_LICENSE_ENFORCE = "true"
$env:XIGUA_AUTH_SERVER_URL = "https://your-auth.example.com"
```
