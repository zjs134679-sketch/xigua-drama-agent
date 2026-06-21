@echo off
chcp 65001 >nul

cd /d "%~dp0.."
set ROOT=%CD%

echo [启动] 项目根目录: %ROOT%

:: ===== 后端 backend (port 5678) =====
if not exist "%ROOT%\backend\.venv\Scripts\python.exe" (
    echo [后端] 创建虚拟环境...
    python -m venv "%ROOT%\backend\.venv"
    echo [后端] 安装依赖...
    "%ROOT%\backend\.venv\Scripts\pip.exe" install -q -r "%ROOT%\backend\requirements.txt"
)
echo [后端] 启动中...
start "xigua-backend" /d "%ROOT%\backend" "%ROOT%\backend\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 5678

:: ===== 前端 frontend (port 5173) =====
if not exist "%ROOT%\frontend\node_modules" (
    echo [前端] 安装 npm 依赖...
    pushd "%ROOT%\frontend"
    npm install
    popd
)
echo [前端] 启动中...
start "xigua-frontend" /d "%ROOT%\frontend" cmd /c "npm run dev"

:: ===== auth-server (port 8100) — 认证/封号/合规词库下发 =====
if not exist "%ROOT%\auth-server\.venv\Scripts\python.exe" (
    echo [认证] 创建虚拟环境...
    python -m venv "%ROOT%\auth-server\.venv"
    echo [认证] 安装依赖...
    "%ROOT%\auth-server\.venv\Scripts\pip.exe" install -q -r "%ROOT%\auth-server\requirements.txt"
)
echo [认证] 启动中...
start "xigua-auth" /d "%ROOT%\auth-server" "%ROOT%\auth-server\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8100

echo.
echo ==============================
echo  后端 API Docs : http://127.0.0.1:5678/docs
echo  前端界面      : http://localhost:5173
echo  认证服务      : http://127.0.0.1:8100
echo ==============================
echo.
pause
