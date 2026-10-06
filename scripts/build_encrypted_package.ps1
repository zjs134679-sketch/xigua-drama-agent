# =============================================================================
# 西瓜短剧Agent — 加密安装包构建脚本
# 输出：<仓库根>\anzhuangbao\（默认，可用 -OutputDir 覆盖）
# =============================================================================
param(
    [string]$OutputDir = "",
    [string]$Password = "",
    [string]$Version = "0.1.2"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Resolve-Path "$scriptDir\.."
if (-not $OutputDir) { $OutputDir = Join-Path $projectRoot "anzhuangbao" }
$releaseDir = "$projectRoot\release"
$stagingDir = "$OutputDir\staging"
$packageName = "西瓜短剧Agent-Setup-$Version"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

# ---------- 密码处理 ----------
if (-not $Password) {
    # 生成强密码（16 字节 → 32 hex）
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes = New-Object byte[] 16
    $rng.GetBytes($bytes)
    $Password = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " 西瓜短剧Agent — 加密安装包构建" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "版本: $Version"
Write-Host "输出: $OutputDir"
Write-Host "（安装密码仅写入 密码-请妥善保管.txt，不在控制台显示）" -ForegroundColor Gray
Write-Host "========================================" -ForegroundColor Cyan

# ---------- 1. 准备输出目录 ----------
Write-Host "`n[1/5] 准备输出目录..." -ForegroundColor Yellow
if (Test-Path $OutputDir) {
    Remove-Item -Recurse -Force "$OutputDir\staging" -ErrorAction SilentlyContinue
    Remove-Item -Force "$OutputDir\*.zip" -ErrorAction SilentlyContinue
    Remove-Item -Force "$OutputDir\*.enc" -ErrorAction SilentlyContinue
    Remove-Item -Force "$OutputDir\*.ps1" -ErrorAction SilentlyContinue
    Remove-Item -Force "$OutputDir\*.bat" -ErrorAction SilentlyContinue
    Remove-Item -Force "$OutputDir\*.txt" -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
New-Item -ItemType Directory -Force -Path $stagingDir | Out-Null

# ---------- 2. 复制并清理 ----------
Write-Host "`n[2/5] 复制发布文件并清理敏感数据..." -ForegroundColor Yellow

# 复制 release 目录所有内容到 staging
Copy-Item -Recurse -Force "$releaseDir\*" $stagingDir

# --- 清理敏感/无关文件 ---
$toRemove = @(
    # 本地字典定制（可能含敏感词）
    "$stagingDir\dict\*.local.txt",
    # 空日志目录（安装后自动创建）
    "$stagingDir\logs",
    # 示例配置里的敏感路径引用
    # （会被后面的文本替换处理）
    # Inno Setup 产物（仅匹配安装包 exe，不删 xigua-backend.exe）
    "$stagingDir\西瓜短剧Agent-Setup-*.exe"
)
foreach ($pattern in $toRemove) {
    Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

# --- 清理使用说明中的个人路径 ---
$readmePath = "$stagingDir\使用说明.txt"
if (Test-Path $readmePath) {
    $content = Get-Content $readmePath -Raw -Encoding UTF8
    # 移除个人路径引用，替换为通用说明
    $content = $content -replace 'E:\\xigua Agent  密码管理\\一键启动\.bat', '授权服务器（auth-server）'
    $content = $content -replace '端口 8100', '端口 8100，请自行启动授权服务'
    Set-Content -Path $readmePath -Value $content -Encoding UTF8 -NoNewline
    Write-Host "  已清理: 使用说明.txt 中的个人路径" -ForegroundColor Green
}

# --- 确认无 API Key 硬编码 ---
Write-Host "  扫描敏感关键字..." -ForegroundColor Gray
$sensitivePatterns = @(
    'sk-[a-zA-Z0-9]{20,}',
    'XIGUA_LLM_API_KEY\s*=\s*[A-Za-z0-9_\-]+(?!\s*$|dev|example|change)',
    'api_key\s*=\s*["''][A-Za-z0-9_\-]{20,}["'']'
)
$foundSensitive = $false
foreach ($pattern in $sensitivePatterns) {
    $matches = Get-ChildItem -Path $stagingDir -Recurse -File -Exclude "*.exe","*.dll","*.ico","*.png","*.mp4" |
        Select-String -Pattern $pattern -AllMatches -ErrorAction SilentlyContinue
    if ($matches) {
        Write-Host "  警告: 可能含敏感数据:" -ForegroundColor Red
        foreach ($m in $matches) {
            Write-Host "    $($m.Path):$($m.LineNumber)" -ForegroundColor Red
        }
        $foundSensitive = $true
    }
}
if (-not $foundSensitive) {
    Write-Host "  未发现硬编码 API Key ✓" -ForegroundColor Green
}

# --- 删除空目录 ---
Get-ChildItem -Path $stagingDir -Recurse -Directory |
    Where-Object { @(Get-ChildItem -Path $_.FullName -Recurse -File).Count -eq 0 } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# ---------- 3. 创建 ZIP ----------
Write-Host "`n[3/5] 压缩为 ZIP..." -ForegroundColor Yellow
$zipPath = "$OutputDir\$packageName.zip"
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }

Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($stagingDir, $zipPath,
    [System.IO.Compression.CompressionLevel]::Optimal, $false)

$zipSize = (Get-Item $zipPath).Length
Write-Host "  ZIP 大小: $([math]::Round($zipSize/1MB, 2)) MB" -ForegroundColor Green

# ---------- 4. AES-256 加密 ----------
Write-Host "`n[4/5] AES-256 加密..." -ForegroundColor Yellow

$zipBytes = [System.IO.File]::ReadAllBytes($zipPath)
$aes = [System.Security.Cryptography.Aes]::Create()
$aes.KeySize = 256
$aes.Mode = [System.Security.Cryptography.CipherMode]::CBC
$aes.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7

# 用 PBKDF2 从密码派生密钥
$salt = New-Object byte[] 16
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($salt)

$derive = New-Object System.Security.Cryptography.Rfc2898DeriveBytes($Password, $salt, 100000)
$aes.Key = $derive.GetBytes(32)  # 256-bit key
$aes.IV = $derive.GetBytes(16)   # 128-bit IV

$encryptor = $aes.CreateEncryptor()
$encryptedBytes = $encryptor.TransformFinalBlock($zipBytes, 0, $zipBytes.Length)

# 写入加密文件：[salt(16)][IV(16)][ciphertext]
$encPath = "$OutputDir\$packageName.enc"
$fs = [System.IO.File]::OpenWrite($encPath)
$fs.Write($salt, 0, $salt.Length)
$fs.Write($aes.IV, 0, $aes.IV.Length)
$fs.Write($encryptedBytes, 0, $encryptedBytes.Length)
$fs.Close()

$aes.Dispose()
$encSize = (Get-Item $encPath).Length
Write-Host "  加密文件: $encPath" -ForegroundColor Green
Write-Host "  加密大小: $([math]::Round($encSize/1MB, 2)) MB" -ForegroundColor Green

# ---------- 5. 生成安装脚本 ----------
Write-Host "`n[5/5] 生成安装脚本..." -ForegroundColor Yellow

# --- 解密安装 PowerShell 脚本 ---
$installPs1 = @"
# =============================================================================
# 西瓜短剧Agent — 加密安装器
# 双击运行即可安装（需要 PowerShell 5.1+）
# =============================================================================
`$ErrorActionPreference = "Continue"
`$Host.UI.RawUI.WindowTitle = "西瓜短剧Agent 安装程序"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " 西瓜短剧Agent v$Version — 安装程序" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 安装目录
`$installDir = "`$env:LOCALAPPDATA\Programs\XiguaDramaAgent"
Write-Host ""
Write-Host "安装目录: `$installDir"
Write-Host ""

# 解密
`$scriptDir = Split-Path -Parent `$MyInvocation.MyCommand.Path
`$encFile = Join-Path `$scriptDir "$packageName.enc"

if (-not (Test-Path `$encFile)) {
    Write-Host "错误: 找不到加密数据文件" -ForegroundColor Red
    Write-Host "请确保 $packageName.enc 与本脚本在同一目录" -ForegroundColor Red
    pause
    exit 1
}

# A1 修复：安装密码不再嵌入脚本，安装时向用户索取（密码通过密码文件线下交付）
Write-Host ""
`$secPwd = Read-Host "请输入安装密码" -AsSecureString
if (-not `$secPwd) {
    Write-Host "未输入密码，安装取消。" -ForegroundColor Red
    pause
    exit 1
}
`$__ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR(`$secPwd)
try {
    `$installPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(`$__ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR(`$__ptr)
}
`$secPwd = `$null

Write-Host "正在解密安装包..." -ForegroundColor Yellow

`$encBytes = [System.IO.File]::ReadAllBytes(`$encFile)
`$salt = `$encBytes[0..15]
`$iv = `$encBytes[16..31]
`$ciphertext = `$encBytes[32..(`$encBytes.Length - 1)]

`$derive = New-Object System.Security.Cryptography.Rfc2898DeriveBytes(
    `$installPassword, `$salt, 100000)
`$key = `$derive.GetBytes(32)
`$derive.Dispose()
`$installPassword = `$null

`$aes = [System.Security.Cryptography.Aes]::Create()
`$aes.KeySize = 256
`$aes.Mode = [System.Security.Cryptography.CipherMode]::CBC
`$aes.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
`$aes.Key = `$key
`$aes.IV = `$iv

`$decryptor = `$aes.CreateDecryptor()
try {
    `$zipBytes = `$decryptor.TransformFinalBlock(`$ciphertext, 0, `$ciphertext.Length)
} catch {
    Write-Host "解密失败！密码错误或文件已损坏。" -ForegroundColor Red
    pause
    exit 1
}
`$aes.Dispose()

# 解压到安装目录
Write-Host "正在解压..." -ForegroundColor Yellow

if (Test-Path `$installDir) {
    Write-Host "  备份旧版本..." -ForegroundColor Gray
    `$backupDir = "`$installDir.bak.$(Get-Date -Format 'yyyyMMddHHmmss')"
    try {
        # 先停掉旧进程
        Get-Process -Name "xigua-backend" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
        Rename-Item `$installDir `$backupDir -ErrorAction SilentlyContinue
    } catch {
        Write-Host "  无法移除旧目录，尝试覆盖安装..." -ForegroundColor Yellow
    }
}

New-Item -ItemType Directory -Force -Path `$installDir | Out-Null

Add-Type -AssemblyName System.IO.Compression.FileSystem
`$ms = New-Object System.IO.MemoryStream(, `$zipBytes)
`$zip = [System.IO.Compression.ZipArchive]::new(`$ms)
`$zip.Entries | ForEach-Object {
    `$dest = Join-Path `$installDir `$_.FullName
    if (`$_.Name -eq '') {
        # 目录
        New-Item -ItemType Directory -Force -Path `$dest | Out-Null
    } else {
        `$dir = Split-Path `$dest -Parent
        if (-not (Test-Path `$dir)) {
            New-Item -ItemType Directory -Force -Path `$dir | Out-Null
        }
        try {
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile(`$_ , `$dest, `$true)
        } catch {}
    }
}
`$zip.Dispose()
`$ms.Dispose()

# 创建桌面快捷方式
Write-Host "正在创建快捷方式..." -ForegroundColor Yellow
`$desktop = [Environment]::GetFolderPath("Desktop")
`$shortcutPath = Join-Path `$desktop "西瓜短剧Agent.lnk"
try {
    `$WshShell = New-Object -ComObject WScript.Shell
    `$shortcut = `$WshShell.CreateShortcut(`$shortcutPath)
    `$shortcut.TargetPath = Join-Path `$installDir "打开西瓜短剧.cmd"
    `$shortcut.WorkingDirectory = `$installDir
    `$shortcut.IconLocation = Join-Path `$installDir "app.ico"
    `$shortcut.Save()
    Write-Host "  桌面快捷方式已创建" -ForegroundColor Green
} catch {
    Write-Host "  快捷方式创建失败（可手动创建）" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " 安装完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "启动方式："
Write-Host "  1. 双击桌面「西瓜短剧Agent」"
Write-Host "  2. 或运行: `$installDir\打开西瓜短剧.cmd"
Write-Host ""
Write-Host "配置文件: `$installDir\config.env （首次运行自动创建）"
Write-Host ""

# 询问是否启动
`$choice = Read-Host "是否立即启动？(Y/n)"
if (`$choice -ne 'n' -and `$choice -ne 'N') {
    Write-Host "正在启动..." -ForegroundColor Yellow
    Start-Process -FilePath (Join-Path `$installDir "打开西瓜短剧.cmd") -WorkingDirectory `$installDir
}

pause
"@

$installPs1Path = "$OutputDir\安装-西瓜短剧Agent.ps1"
Set-Content -Path $installPs1Path -Value $installPs1 -Encoding UTF8
Write-Host "  安装脚本: $installPs1Path" -ForegroundColor Green

# --- 一键安装 .bat 包装器 ---
$installBat = @"
@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 西瓜短剧Agent 安装程序
echo.
echo   西瓜短剧Agent v$Version
echo   正在启动安装程序...
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0安装-西瓜短剧Agent.ps1"
pause
"@

$installBatPath = "$OutputDir\安装-西瓜短剧Agent.bat"
Set-Content -Path $installBatPath -Value $installBat -Encoding ASCII
Write-Host "  一键安装: $installBatPath" -ForegroundColor Green

# --- 密码文件（妥善保管） ---
$pwdFile = "$OutputDir\密码-请妥善保管.txt"
$pwdContent = @"
========================================
西瓜短剧Agent v$Version — 安装密码
========================================
生成时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
安装密码: $Password

请妥善保管此密码！
安装时需要此密码才能解密安装包。

========================================
"@
Set-Content -Path $pwdFile -Value $pwdContent -Encoding UTF8
Write-Host "  密码文件: $pwdFile" -ForegroundColor Yellow

# --- 清理 ---
Write-Host "`n清理临时文件..." -ForegroundColor Gray
Remove-Item -Recurse -Force $stagingDir -ErrorAction SilentlyContinue
Remove-Item -Force $zipPath -ErrorAction SilentlyContinue

# --- 完成 ---
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " 构建完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "输出目录: $OutputDir"
Write-Host ""
Write-Host "文件列表:" -ForegroundColor White
Get-ChildItem $OutputDir | ForEach-Object {
    $size = if ($_.Length -gt 1MB) { "$([math]::Round($_.Length/1MB,2)) MB" } elseif ($_.Length -gt 1KB) { "$([math]::Round($_.Length/1KB,1)) KB" } else { "$($_.Length) B" }
    Write-Host "  $($_.Name)  ($size)" -ForegroundColor Gray
}
Write-Host ""
Write-Host "交付给用户时，将以下文件打包发送：" -ForegroundColor Cyan
Write-Host "  1. $packageName.enc  （加密安装包）" -ForegroundColor White
Write-Host "  2. 安装-西瓜短剧Agent.bat  （双击运行安装）" -ForegroundColor White
Write-Host "  3. 安装-西瓜短剧Agent.ps1  （安装脚本）" -ForegroundColor White
Write-Host ""
Write-Host "安装密码已保存到 密码-请妥善保管.txt（请线下单独交付用户）" -ForegroundColor Yellow
Write-Host ""
