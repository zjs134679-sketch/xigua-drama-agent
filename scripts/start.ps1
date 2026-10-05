$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogDir = Join-Path $Root "logs"
$RunDir = Join-Path $Root ".run"
New-Item -ItemType Directory -Force -Path $LogDir, $RunDir | Out-Null

function Write-Step([string]$Message) {
    Write-Host "[西瓜短剧] $Message" -ForegroundColor Cyan
}

function Test-Url([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    } catch {
        return $false
    }
}

function Wait-Url([string]$Name, [string]$Url, [int]$Seconds = 90) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Url $Url) {
            Write-Host "  OK  $Name" -ForegroundColor Green
            return
        }
        Start-Sleep -Milliseconds 750
    }
    throw "$Name 启动超时。请查看 logs 目录。"
}

function Get-PortOwner([int]$Port) {
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $connection) { return $null }
    return Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)" -ErrorAction SilentlyContinue
}

function Clear-StaleProjectPort([string]$Name, [int]$Port, [string]$HealthUrl) {
    $owner = Get-PortOwner $Port
    if (-not $owner) { return $false }
    if (Test-Url $HealthUrl) {
        Write-Host "  OK  $Name 已在运行 (端口 $Port)" -ForegroundColor Green
        return $true
    }
    $command = [string]$owner.CommandLine
    $executable = [string]$owner.ExecutablePath
    if ($command.IndexOf($Root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
        $executable.IndexOf($Root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        Write-Step "清理失效的 $Name 进程 PID=$($owner.ProcessId)"
        & taskkill.exe /PID $owner.ProcessId /T /F | Out-Null
        Start-Sleep -Seconds 1
        return $false
    }
    throw "端口 $Port 被其他程序占用 (PID=$($owner.ProcessId))。请关闭该程序后重试。"
}

function Start-LoggedProcess(
    [string]$Name,
    [string]$FilePath,
    [string[]]$ArgumentList,
    [string]$WorkingDirectory
) {
    $stdout = Join-Path $LogDir "$Name.log"
    $stderr = Join-Path $LogDir "$Name-error.log"
    $process = Start-Process -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru
    Set-Content -LiteralPath (Join-Path $RunDir "$Name.pid") -Value $process.Id -Encoding Ascii
}

Write-Step "项目目录：$Root"

$BackendPython = Join-Path $Root "backend\.venv\Scripts\python.exe"
# 授权服务实体在「E:\xigua Agent  密码管理\auth-server」；工程内 auth-server 为联接目录
$AuthRootCandidates = @(
    (Join-Path $Root "auth-server"),
    "E:\xigua Agent  密码管理\auth-server"
)
$AuthRoot = $null
$AuthPython = $null
foreach ($cand in $AuthRootCandidates) {
    $py = Join-Path $cand ".venv\Scripts\python.exe"
    $main = Join-Path $cand "app\main.py"
    if ((Test-Path -LiteralPath $py) -and (Test-Path -LiteralPath $main)) {
        $AuthRoot = $cand
        $AuthPython = $py
        break
    }
}
$Npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not (Test-Path $BackendPython)) { throw "后端环境不存在：请先运行 scripts\build_backend.bat" }
if (-not $AuthPython) { throw "认证环境不存在。请确认「E:\xigua Agent  密码管理\auth-server」完整，或双击该目录 一键启动.bat" }
if (-not $Npm) { throw "未找到 Node.js/npm，请先安装 Node.js" }
if (-not (Test-Path (Join-Path $Root "frontend\node_modules"))) {
    Write-Step "首次安装前端依赖"
    Push-Location (Join-Path $Root "frontend")
    try { & $Npm install; if ($LASTEXITCODE -ne 0) { throw "npm install 失败" } }
    finally { Pop-Location }
}

# Authentication must be ready before backend downloads the compliance dictionary.
$authRunning = Clear-StaleProjectPort "认证服务" 8100 "http://127.0.0.1:8100/health"
if (-not $authRunning) {
    Write-Step "启动认证/注册服务 :8100  ($AuthRoot)"
    Start-LoggedProcess "auth" $AuthPython @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8100") $AuthRoot
    Wait-Url "认证服务" "http://127.0.0.1:8100/health"
}

$backendRunning = Clear-StaleProjectPort "后端服务" 5678 "http://127.0.0.1:5678/openapi.json"
if (-not $backendRunning) {
    Write-Step "启动后端服务"
    Start-LoggedProcess "backend" $BackendPython @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "5678") (Join-Path $Root "backend")
    Wait-Url "后端服务" "http://127.0.0.1:5678/openapi.json"
}

$frontendRunning = Clear-StaleProjectPort "前端服务" 5173 "http://127.0.0.1:5173"
if (-not $frontendRunning) {
    Write-Step "启动前端服务（固定端口 5173）"
    Start-LoggedProcess "frontend" $Npm @("run", "dev", "--", "--host", "127.0.0.1", "--port", "5173", "--strictPort") (Join-Path $Root "frontend")
    Wait-Url "前端服务" "http://127.0.0.1:5173" 120
}

Write-Host ""
Write-Host "全部服务健康，正在打开：http://127.0.0.1:5173" -ForegroundColor Green
Start-Process "http://127.0.0.1:5173"
Start-Sleep -Seconds 2
