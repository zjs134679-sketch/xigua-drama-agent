#Requires -Version 5.1
<#
.SYNOPSIS
  西瓜短剧 Agent —— 发布构建（前端 minify + 后端 Nuitka + Inno Setup 安装包）

.DESCRIPTION
  产物：release\西瓜短剧Agent-Setup-x.x.x.exe
  安装后默认：%LOCALAPPDATA%\Programs\XiguaDramaAgent
  桌面图标：启动西瓜短剧.vbs + app.ico
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "backend"))) { $Root = Split-Path -Parent $PSScriptRoot }
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Release = Join-Path $Root "release"
$Version = "0.1.1"
$Py = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }
$Iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $Iscc)) {
  $Iscc = "C:\Program Files\Inno Setup 6\ISCC.exe"
}

function Write-Step($msg) { Write-Host "`n==== $msg ====" -ForegroundColor Cyan }

# ---------- 清理并准备 release ----------
Write-Step "准备 release 目录"
if (Test-Path $Release) {
  Get-ChildItem $Release -Force | Where-Object {
    $_.Name -like "西瓜短剧Agent-Setup-*.exe" -or $_.Name -eq "xigua-backend.exe"
  } | Remove-Item -Force -ErrorAction SilentlyContinue
  # 清空其它内容但保留目录
  Get-ChildItem $Release -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $Release | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Release "web") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Release "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Release "skills") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Release "logs") | Out-Null

# ---------- 图标 app.ico ----------
Write-Step "生成 app.ico"
$logoPng = Join-Path $Frontend "public\logo.png"
if (-not (Test-Path $logoPng)) { $logoPng = Join-Path $Frontend "dist\logo.png" }
$icoPath = Join-Path $Release "app.ico"
& $Py -c @"
from pathlib import Path
import struct, zlib

src = Path(r'''$logoPng''')
dst = Path(r'''$icoPath''')

def png_to_ico(png_path: Path, ico_path: Path) -> None:
    data = png_path.read_bytes()
    # Minimal ICO that embeds PNG (Vista+)
    # ICONDIR + ICONDIRENTRY + PNG bytes
    count = 1
    header = struct.pack('<HHH', 0, 1, count)
    # width/height 0 means 256 in ICO
    entry = struct.pack('<BBBBHHII', 0, 0, 0, 0, 1, 32, len(data), 6 + 16)
    ico_path.write_bytes(header + entry + data)
    print('ico written', ico_path, 'bytes', ico_path.stat().st_size)

if src.is_file():
    png_to_ico(src, dst)
else:
    # 1x1 blue PNG fallback
    import base64
    tiny = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
    )
    png_to_ico_bytes = tiny
    header = struct.pack('<HHH', 0, 1, 1)
    entry = struct.pack('<BBBBHHII', 1, 1, 0, 0, 1, 32, len(png_to_ico_bytes), 22)
    dst.write_bytes(header + entry + png_to_ico_bytes)
    print('fallback ico', dst)
"@
if (-not (Test-Path $icoPath)) { throw "生成 app.ico 失败" }

# ---------- 前端 ----------
Write-Step "构建前端 (vite production)"
Push-Location $Frontend
if (-not (Test-Path "node_modules")) { npm ci 2>$null; if (-not $?) { npm install } }
npm run build
if (-not (Test-Path "dist\index.html")) { throw "前端构建失败：无 dist/index.html" }
Copy-Item -Path "dist\*" -Destination (Join-Path $Release "web") -Recurse -Force
Pop-Location
Write-Host "前端 -> release\web"

# ---------- 技能与数据 ----------
Write-Step "复制技能包与字典"
$skillsSrc = Join-Path $Root "data\skills"
if (Test-Path $skillsSrc) {
  Copy-Item -Path "$skillsSrc\*" -Destination (Join-Path $Release "skills") -Recurse -Force
}
$dictSrc = Join-Path $Backend "dict"
if (Test-Path $dictSrc) {
  Copy-Item -Path $dictSrc -Destination (Join-Path $Release "dict") -Recurse -Force
}
$wfSrc = Join-Path $Backend "app\workflows"
if (Test-Path $wfSrc) {
  New-Item -ItemType Directory -Force -Path (Join-Path $Release "app\workflows") | Out-Null
  Copy-Item -Path "$wfSrc\*" -Destination (Join-Path $Release "app\workflows") -Recurse -Force
}

# ---------- 能力票验签公钥（Ed25519，A2 修复） ----------
# 客户端不再持有 JWT 签名密钥；构建时从 auth-server 的 Ed25519 私钥派生公钥写入配置。
Write-Step "派生能力票验签公钥（Ed25519）"
$capPriv = $env:XIGUA_CAPABILITY_PRIVKEY
if (-not $capPriv) {
  throw "缺少环境变量 XIGUA_CAPABILITY_PRIVKEY（auth-server 能力票 Ed25519 私钥，32 字节 hex/base64）。请先在 auth-server 侧生成（例：python -c `"import secrets;print(secrets.token_hex(32))`"）并设置，再重新构建。"
}
$capPub = & $Py -c @"
import base64, os, sys
raw = os.environ.get('XIGUA_CAPABILITY_PRIVKEY', '').strip()
data = None
if raw and len(raw) == 64 and all(c in '0123456789abcdefABCDEF' for c in raw):
    data = bytes.fromhex(raw)
else:
    try:
        data = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))
    except Exception:
        data = None
if not data or len(data) != 32:
    sys.exit('XIGUA_CAPABILITY_PRIVKEY 必须是 32 字节（hex 或 base64）')
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
pub = Ed25519PrivateKey.from_private_bytes(data).public_key()
print(base64.b64encode(pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode())
"@
$capPub = ($capPub | Out-String).Trim()
if (-not $capPub) { throw "能力票公钥派生失败：请确认 backend .venv 已安装 cryptography" }
Write-Host "XIGUA_CAPABILITY_PUBKEY=$capPub" -ForegroundColor Gray

# ---------- 生产环境配置模板 ----------
Write-Step "写入生产配置模板"
@"
# 西瓜短剧 Agent 生产配置（安装后自动复制为 config.env）
# 详见仓库 docs/auth-url-deploy.md

XIGUA_LICENSE_ENFORCE=true

# 授权服地址：用户打开软件后登录/卡密访问这里
# 自测本机：
XIGUA_AUTH_SERVER_URL=http://127.0.0.1:8100
# 卖客户时改成公网，例如：
# XIGUA_AUTH_SERVER_URL=https://auth.example.com

# 能力票验签公钥（Ed25519，base64）：构建时已从 auth-server 私钥自动派生填入，无需手动改。
# 客户端不再持有签名密钥；旧 HS256 能力票作废，升级后用户需重新登录。
XIGUA_CAPABILITY_PUBKEY=$capPub
"@ | Set-Content -Path (Join-Path $Release "config.env.example") -Encoding UTF8

# E1：生产包断言 —— license_enforce 必须为 true，否则门禁全关。构建直接失败，不静默放行。
$cfgCheck = Get-Content -Path (Join-Path $Release "config.env.example") -Raw
if ($cfgCheck -notmatch '(?m)^XIGUA_LICENSE_ENFORCE=true\s*$') {
    throw "E1 断言失败：config.env.example 的 XIGUA_LICENSE_ENFORCE 不是 true，拒绝打包（生产包必须开启授权门禁）。"
}

# ---------- 启动器（BAT + VBS，带健康检查） ----------
Write-Step "写入启动器"
# ASCII-safe bat: set env, start exe, wait for /health, open browser
$launcherBat = @'
@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title XiguaDramaAgent

if exist "%~dp0config.env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%~dp0config.env") do (
    if not "%%a"=="" set "%%a=%%b"
  )
)

if not defined XIGUA_LICENSE_ENFORCE set "XIGUA_LICENSE_ENFORCE=true"
if not defined XIGUA_AUTH_SERVER_URL set "XIGUA_AUTH_SERVER_URL=http://127.0.0.1:8100"
set "XIGUA_FRONTEND_DIST=%~dp0web"
set "XIGUA_SKILLS_DIR=%~dp0skills"
if not defined LOCALAPPDATA set "LOCALAPPDATA=%USERPROFILE%\AppData\Local"
set "XIGUA_DATA_DIR=%LOCALAPPDATA%\XiguaDramaAgent\data"
set "XIGUA_LOG_DIR=%LOCALAPPDATA%\XiguaDramaAgent\logs"
if not exist "%XIGUA_DATA_DIR%" mkdir "%XIGUA_DATA_DIR%"
if not exist "%XIGUA_LOG_DIR%" mkdir "%XIGUA_LOG_DIR%"
if not exist "%~dp0logs" mkdir "%~dp0logs"

echo [Xigua] Starting backend...
echo [Xigua] Auth: %XIGUA_AUTH_SERVER_URL%
echo [Xigua] Open http://127.0.0.1:5678/

if not exist "%~dp0xigua-backend.exe" (
  echo ERROR: xigua-backend.exe missing. Please reinstall.
  pause
  exit /b 1
)

REM If already healthy, just open browser
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -Uri 'http://127.0.0.1:5678/health' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
if %ERRORLEVEL%==0 (
  start "" "http://127.0.0.1:5678/"
  echo Already running.
  if /i not "%~1"=="/silent" pause
  exit /b 0
)

REM start with empty title so paths with spaces work
start "xigua-backend" /D "%~dp0" /MIN "%~dp0xigua-backend.exe"

REM wait up to ~30s for health
set /a _n=0
:waitloop
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -Uri 'http://127.0.0.1:5678/health' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
if %ERRORLEVEL%==0 goto ready
set /a _n+=1
if %_n% GEQ 30 goto fail
timeout /t 1 /nobreak >nul
goto waitloop

:ready
start "" "http://127.0.0.1:5678/"
echo Started OK.
if /i not "%~1"=="/silent" (
  echo Close this window does NOT stop backend. Use 停止.bat to stop.
  pause
)
exit /b 0

:fail
echo ERROR: backend did not become healthy in 30s.
echo Check log: %XIGUA_LOG_DIR%\backend-crash.log
if exist "%XIGUA_LOG_DIR%\backend-crash.log" type "%XIGUA_LOG_DIR%\backend-crash.log"
pause
exit /b 1
'@
# Write bat as ANSI/Default for cmd compatibility
[System.IO.File]::WriteAllText((Join-Path $Release "启动西瓜短剧.bat"), $launcherBat, [System.Text.Encoding]::GetEncoding(936))

# VBS: silent launcher for desktop icon (no black window flash)
$launcherVbs = @'
' XiguaDramaAgent silent launcher (desktop shortcut target)
Option Explicit
Dim sh, fso, dir, bat, rc
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
bat = dir & "\启动西瓜短剧.bat"
If Not fso.FileExists(bat) Then
  MsgBox "找不到启动脚本：" & bat, 16, "西瓜短剧Agent"
  WScript.Quit 1
End If
' 0 = hide window; True = wait
rc = sh.Run("cmd /c """ & bat & """ /silent", 0, True)
If rc <> 0 Then
  ' show window so user can see error
  sh.Run "cmd /k """ & bat & """", 1, False
End If
'@
[System.IO.File]::WriteAllText((Join-Path $Release "启动西瓜短剧.vbs"), $launcherVbs, [System.Text.Encoding]::Unicode)

$stopper = @'
@echo off
taskkill /FI "WINDOWTITLE eq xigua-backend*" /F >nul 2>&1
taskkill /IM xigua-backend.exe /F >nul 2>&1
echo Backend stop requested.
timeout /t 2 >nul
pause
'@
[System.IO.File]::WriteAllText((Join-Path $Release "停止.bat"), $stopper, [System.Text.Encoding]::GetEncoding(936))

# ---------- Nuitka ----------
Write-Step "安装/检查 Nuitka 并构建后端 exe"
& $Py -m pip install -q "nuitka>=2.0" ordered-set zstandard "cryptography>=41"
$nuitkaOk = $false
Push-Location $Backend
$env:XIGUA_FRONTEND_DIST = Join-Path $Release "web"

# locate pypinyin data for explicit include
$pypinyinDir = & $Py -c "import pypinyin, pathlib; print(pathlib.Path(pypinyin.__file__).parent)"
$pypinyinDir = ($pypinyinDir | Out-String).Trim()
Write-Host "pypinyin dir: $pypinyinDir"

$nuitkaArgs = @(
  "-m", "nuitka", "server_entry.py",
  "--standalone",
  "--onefile",
  "--assume-yes-for-downloads",
  "--output-dir=dist",
  "--output-filename=xigua-backend.exe",
  "--include-package=app",
  "--include-package=fastapi",
  "--include-package=starlette",
  "--include-package=uvicorn",
  "--include-package=pydantic",
  "--include-package=pydantic_settings",
  "--include-package=sqlalchemy",
  "--include-package=httpx",
  "--include-package=anyio",
  "--include-package=jwt",
  "--include-package=cryptography",
  "--include-package=pypinyin",
  "--include-package=multipart",
  "--include-package=python_multipart",
  "--include-package=edge_tts",
  "--include-package-data=pypinyin",
  "--include-data-dir=app/workflows=app/workflows",
  "--include-data-dir=dict=dict",
  "--include-data-dir=app/services/agents/skills=app/services/agents/skills",
  "--windows-company-name=XiguaDrama",
  "--windows-product-name=XiguaDramaAgent",
  "--windows-file-version=$Version.0",
  "--windows-product-version=$Version.0",
  # 不禁用控制台：便于启动失败时看到日志；桌面快捷方式用 VBS 隐藏窗口
  "--windows-console-mode=attach"
  # 注意：不在这里 --windows-icon-from-ico（杀软常锁 DLL 导致 FATAL add resources）
  # 桌面/开始菜单图标由 Inno 的 app.ico 负责
)

try {
  & $Py @nuitkaArgs
  if ($LASTEXITCODE -ne 0) {
    Write-Host "Nuitka exit code: $LASTEXITCODE" -ForegroundColor Yellow
  }
  if (Test-Path "dist\xigua-backend.exe") {
    Copy-Item "dist\xigua-backend.exe" (Join-Path $Release "xigua-backend.exe") -Force
    $nuitkaOk = $true
    Write-Host "Nuitka OK: release\xigua-backend.exe" -ForegroundColor Green
  }
} catch {
  Write-Host "Nuitka failed: $_" -ForegroundColor Yellow
}
Pop-Location

if (-not $nuitkaOk) {
  throw "Nuitka 构建失败，无法生成可用安装包。请检查 backend\.venv 依赖与编译器（可暂时关闭实时防护后重试）。"
}

# ---------- README ----------
@"
西瓜短剧 Agent 发布包 v$Version

【安装】
运行 西瓜短剧Agent-Setup-$Version.exe
默认安装到：%LOCALAPPDATA%\Programs\XiguaDramaAgent
安装后桌面会出现「西瓜短剧Agent」图标。

【使用前】
1. 启动授权服务（密码管理目录「一键启动.bat」，端口 8100）
2. 双击桌面图标启动
3. 浏览器打开 http://127.0.0.1:5678/ 注册/登录/激活

【配置】
安装目录 config.env：
- XIGUA_AUTH_SERVER_URL
- XIGUA_CAPABILITY_PUBKEY（构建时自动填入，无需手动改）

【数据目录】
用户数据写在：%LOCALAPPDATA%\XiguaDramaAgent\data
崩溃日志：%LOCALAPPDATA%\XiguaDramaAgent\logs\backend-crash.log

【停止】
开始菜单或安装目录「停止.bat」
"@ | Set-Content -Path (Join-Path $Release "使用说明.txt") -Encoding UTF8

# ---------- 冒烟：启动 exe 测 /health ----------
Write-Step "冒烟测试 xigua-backend.exe"
Get-Process -Name "xigua-backend" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
$exe = Join-Path $Release "xigua-backend.exe"
$smokeOk = $false
$p = Start-Process -FilePath $exe -WorkingDirectory $Release -PassThru -WindowStyle Minimized
try {
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 1
    if ($p.HasExited) { break }
    try {
      $r = Invoke-WebRequest -Uri "http://127.0.0.1:5678/health" -UseBasicParsing -TimeoutSec 2
      if ($r.StatusCode -eq 200) { $smokeOk = $true; break }
    } catch {}
  }
} finally {
  Get-Process -Name "xigua-backend" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
}
if (-not $smokeOk) {
  $crash = Join-Path $env:LOCALAPPDATA "XiguaDramaAgent\logs\backend-crash.log"
  if (Test-Path $crash) { Write-Host (Get-Content $crash -Raw) -ForegroundColor Red }
  throw "冒烟测试失败：xigua-backend.exe 未能响应 /health"
}
Write-Host "Smoke OK: /health 200" -ForegroundColor Green

# ---------- Inno Setup ----------
Write-Step "编译 Inno Setup 安装包"
$iss = Join-Path $Root "installer\xigua-setup.iss"
if (-not (Test-Path $Iscc)) {
  throw "未找到 Inno Setup 6 ISCC.exe"
}
if (-not (Test-Path $iss)) {
  throw "缺少 installer\xigua-setup.iss"
}
& $Iscc $iss
$setup = Get-ChildItem (Join-Path $Root "release") -Filter "西瓜短剧Agent-Setup*.exe" -EA SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $setup) { throw "Inno 编译完成但未找到安装包" }
Write-Host "安装包: $($setup.FullName)  ($([math]::Round($setup.Length/1MB,1)) MB)" -ForegroundColor Green

Write-Step "完成"
Get-ChildItem $Release | Format-Table Name, Length, LastWriteTime
Write-Host "目录: $Release"
Write-Host "安装包: $($setup.FullName)"
