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

## Inno Setup 安装包

TODO：本次不制作安装包，待功能调试完成后再补充 Inno Setup 配置、桌面快捷方式和卸载流程。

## 后续安全项

SQLCipher 数据库加密留作后续版本实现；当前仍使用普通 SQLite，不应将本地数据库视为加密存储。
