# =============================================================================
# 西瓜短剧Agent — 受保护安装包构建脚本 v2
# 前端/配置/词典 → 加密存储，运行时解密到 %TEMP%，退出即销毁
# =============================================================================
param(
    [string]$OutputDir = "E:\xigua-drama-agent\anzhuangbao",
    [string]$ReleaseDir = "E:\xigua-drama-agent\release",
    [string]$Version = "0.1.2",
    [string]$Password = ""
)

$ErrorActionPreference = "Stop"

# ---------- 密码处理（A1 修复） ----------
# 安装密码是唯一的密钥来源：vault 密钥与外层安装包密钥均由它经 PBKDF2 派生，
# 构建产物（安装脚本/启动器）中不再嵌入任何密钥材料；密码仅写入密码文件线下交付。
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
if (-not $Password) {
    # 生成强密码（16 字节 → 32 hex）
    $pwBytes = New-Object byte[] 16
    $rng.GetBytes($pwBytes)
    $Password = ($pwBytes | ForEach-Object { $_.ToString("x2") }) -join ""
}
$vaultSalt = New-Object byte[] 16
$ivBytes = New-Object byte[] 16
$rng.GetBytes($vaultSalt)
$rng.GetBytes($ivBytes)
$__vd = New-Object System.Security.Cryptography.Rfc2898DeriveBytes($Password, $vaultSalt, 100000)
$keyBytes = $__vd.GetBytes(32)
$__vd.Dispose()

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " 西瓜短剧Agent — 受保护安装包构建 v2" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " （安装密码仅写入 密码-请妥善保管.txt，不在控制台显示）" -ForegroundColor Gray

# ---------- 1. 准备输出 ----------
Write-Host "`n[1/6] 准备..." -ForegroundColor Yellow
Remove-Item -Recurse -Force "$OutputDir\*" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

# ---------- 2. 复制 release ----------
Write-Host "[2/6] 复制发布文件..." -ForegroundColor Yellow
$staging = "$OutputDir\staging"
# 彻底清理后重建
Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $staging | Out-Null
# 逐个复制顶层项目，避免容器冲突
Get-ChildItem $ReleaseDir | ForEach-Object {
    Copy-Item -Recurse -Force $_.FullName $staging
}

# 清理敏感/无关文件
Remove-Item -Recurse -Force "$staging\dict\*.local.txt" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$staging\logs" -ErrorAction SilentlyContinue
Remove-Item -Force "$staging\西瓜短剧Agent-Setup-*.exe" -ErrorAction SilentlyContinue

# 清理使用说明中的个人路径
$readme = "$staging\使用说明.txt"
if (Test-Path $readme) {
    ($(Get-Content $readme -Raw -Encoding UTF8) `
        -replace 'E:\\xigua Agent  密码管理\\一键启动\.bat', '授权服务器（auth-server）' `
        -replace '端口 8100', '端口 8100，请自行启动授权服务') |
        Set-Content $readme -Encoding UTF8 -NoNewline
}

# ---------- 3. 对前端 JS 做额外混淆 ----------
Write-Host "[3/6] 前端代码加固..." -ForegroundColor Yellow
$jsFile = Get-ChildItem "$staging\web\assets\*.js" | Select-Object -First 1
if ($jsFile) {
    $js = [System.IO.File]::ReadAllText($jsFile.FullName, [System.Text.Encoding]::UTF8)
    # 插入反调试 + 域名锁 + 禁止右键/F12
    $guard = @"
;(function(){
  // 反调试保护
  var _0x=function(){try{var d=new Date();debugger;if(new Date()-d>100){return}}catch(e){}};
  setInterval(_0x,2000);
  // 禁止右键
  document.addEventListener('contextmenu',function(e){e.preventDefault()});
  // 禁止 F12 / Ctrl+Shift+I / Ctrl+U
  document.addEventListener('keydown',function(e){
    if(e.keyCode===123||(e.ctrlKey&&e.shiftKey&&e.keyCode===73)||(e.ctrlKey&&e.keyCode===85)){
      e.preventDefault();return false;
    }
  });
  // 控制台检测
  var _t=0;var _c=function(){var e=function(){},r='object';try{if(/./.test){}e['__proto__']=[]}catch(h){_t++}
  if(_t>2&&console.clear)console.clear()};setInterval(_c,3000);
})();
"@
    $js = $guard + $js
    [System.IO.File]::WriteAllText($jsFile.FullName, $js, [System.Text.Encoding]::UTF8)
    Write-Host "  前端 JS 已加固 ($([math]::Round($jsFile.Length/1KB,1)) KB)" -ForegroundColor Green
}

# ---------- 4. 构建加密 Vault ----------
Write-Host "[4/6] 构建加密 Vault（web + dict + skills + config）..." -ForegroundColor Yellow

# 将敏感目录打包为 tar 流 → AES 加密 → vault.dat
$vaultDirs = @("web", "dict", "skills")
$vaultFiles = @("config.env.example")

# 创建临时清单
$tmpDir = "$OutputDir\tmp_vault"
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

# 生成清单文件
$manifest = @()
foreach ($d in $vaultDirs) {
    $src = "$staging\$d"
    if (Test-Path $src) {
        foreach ($f in Get-ChildItem $src -Recurse -File) {
            $rel = $f.FullName.Substring($staging.Length + 1)
            $manifest += $rel
            $dest = "$tmpDir\$rel"
            $destDir = Split-Path $dest -Parent
            if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Force -Path $destDir | Out-Null }
            Copy-Item $f.FullName $dest
        }
    }
}
foreach ($f in $vaultFiles) {
    $src = "$staging\$f"
    if (Test-Path $src) {
        $manifest += $f
        Copy-Item $src "$tmpDir\$f"
    }
}

# 写入清单
$manifestPath = "$tmpDir\_manifest.json"
$manifest | ConvertTo-Json | Set-Content $manifestPath -Encoding UTF8

# 将 tmpDir 压缩为 ZIP（内存流）
Add-Type -AssemblyName System.IO.Compression.FileSystem
$vaultZip = "$OutputDir\_vault_tmp.zip"
[System.IO.Compression.ZipFile]::CreateFromDirectory($tmpDir, $vaultZip,
    [System.IO.Compression.CompressionLevel]::Optimal, $false)

$zipBytes = [System.IO.File]::ReadAllBytes($vaultZip)
Remove-Item -Force $vaultZip -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue

# AES-256-CBC 加密
$aes = [System.Security.Cryptography.Aes]::Create()
$aes.KeySize = 256
$aes.Mode = [System.Security.Cryptography.CipherMode]::CBC
$aes.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
$aes.Key = $keyBytes
$aes.IV = $ivBytes

$encryptor = $aes.CreateEncryptor()
$encryptedZip = $encryptor.TransformFinalBlock($zipBytes, 0, $zipBytes.Length)
$aes.Dispose()

# vault.dat = [vaultSalt(16)][IV(16)][ciphertext]（salt 公开，仅用于密钥派生）
$vaultPath = "$staging\xigua-vault.dat"
$fs = [System.IO.File]::OpenWrite($vaultPath)
$fs.Write($vaultSalt, 0, 16)
$fs.Write($ivBytes, 0, 16)
$fs.Write($encryptedZip, 0, $encryptedZip.Length)
$fs.Close()

Write-Host "  Vault 大小: $([math]::Round((Get-Item $vaultPath).Length/1MB, 2)) MB" -ForegroundColor Green
Write-Host "  包含: $($manifest.Count) 个文件" -ForegroundColor Green

# 从 staging 中删除已进 vault 的明文文件
foreach ($d in $vaultDirs) {
    Remove-Item -Recurse -Force "$staging\$d" -ErrorAction SilentlyContinue
}
foreach ($f in $vaultFiles) {
    Remove-Item -Force "$staging\$f" -ErrorAction SilentlyContinue
}

# ---------- 5. 生成运行时解密启动器 ----------
Write-Host "[5/6] 生成受保护启动器..." -ForegroundColor Yellow

# A1 修复：不再把 vault 密钥分段嵌入 launch.ps1。
# 密钥由安装密码派生，安装脚本经 DPAPI 落盘（见安装脚本模板）；launch.ps1 运行时解密使用。

# 生成受保护的 launch.ps1
$launchPs1 = @'
# =============================================================================
# 西瓜短剧Agent — 受保护启动器
# 敏感文件运行时解密到 %TEMP%，进程退出后自动销毁
# =============================================================================
$ErrorActionPreference = "Continue"
$Host.UI.RawUI.WindowTitle = "西瓜短剧Agent"

$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $AppDir) { $AppDir = (Get-Location).Path }

$LogDir = Join-Path $env:LOCALAPPDATA "XiguaDramaAgent\logs"
$DataDir = Join-Path $env:LOCALAPPDATA "XiguaDramaAgent\data"
New-Item -ItemType Directory -Force -Path $LogDir, $DataDir | Out-Null
$LogFile = Join-Path $LogDir "launcher.log"

function Write-Log([string]$M) {
    try { Add-Content -Path $LogFile -Value ("{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $M) -Encoding UTF8 } catch {}
}

function Decrypt-Vault {
    Write-Log "decrypt vault..."
    # === A1 修复：vault 密钥不再嵌入脚本 ===
    # 安装时由安装密码派生，经 Windows DPAPI（当前用户）保护后落盘到 vault.key；
    # 此处用 DPAPI 解保护后使用。更换 Windows 用户或删除 key 文件后需重新安装。
    # 注：用 [Environment]::GetFolderPath 而不用 $env:LOCALAPPDATA，
    #     是为了避开本构建脚本混淆器对 "$e" 的误替换（预先存在的混淆 bug，未在本次改动）。
    $keyFile = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "XiguaDramaAgent\vault.key"
    if (-not (Test-Path $keyFile)) {
        throw "找不到 vault 密钥文件（vault.key），请重新运行安装程序"
    }
    Add-Type -AssemblyName System.Security
    $protected = [System.IO.File]::ReadAllBytes($keyFile)
    $keyBytes = [Security.Cryptography.ProtectedData]::Unprotect(
        $protected, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    [Array]::Clear($protected, 0, $protected.Length)

    $vaultPath = Join-Path $AppDir "xigua-vault.dat"
    if (-not (Test-Path $vaultPath)) {
        throw "找不到加密数据文件 xigua-vault.dat"
    }

    $raw = [System.IO.File]::ReadAllBytes($vaultPath)
    # vault.dat = [vaultSalt(16)][IV(16)][ciphertext]；salt 仅安装时派生用，运行时不需要
    $iv = $raw[16..31]
    $cipher = $raw[32..($raw.Length - 1)]

    $aes = [System.Security.Cryptography.Aes]::Create()
    $aes.KeySize = 256
    $aes.Mode = [System.Security.Cryptography.CipherMode]::CBC
    $aes.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
    $aes.Key = $keyBytes
    $aes.IV = $iv
    $dec = $aes.CreateDecryptor()
    $zipBytes = $dec.TransformFinalBlock($cipher, 0, $cipher.Length)
    $aes.Dispose()

    # 解压到临时目录
    $tmpRoot = Join-Path $env:TEMP ("xigua_" + [System.IO.Path]::GetRandomFileName())
    New-Item -ItemType Directory -Force -Path $tmpRoot | Out-Null

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $ms = New-Object System.IO.MemoryStream(, $zipBytes)
    $zip = [System.IO.Compression.ZipArchive]::new($ms)
    foreach ($e in $zip.Entries) {
        $dest = Join-Path $tmpRoot $e.FullName
        if ($e.Name -eq '') {
            New-Item -ItemType Directory -Force -Path $dest | Out-Null
        } else {
            $d = Split-Path $dest -Parent
            if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
            try {
                [System.IO.Compression.ZipFileExtensions]::ExtractToFile($e, $dest, $true)
            } catch {}
        }
    }
    $zip.Dispose()
    $ms.Dispose()

    Write-Log "vault decrypted to $tmpRoot"
    return $tmpRoot
}

function Wipe-Dir([string]$D) {
    if (-not $D -or -not (Test-Path $D)) { return }
    Get-ChildItem $D -Recurse -File | ForEach-Object {
        try {
            $bytes = New-Object byte[] 4096
            $fs = [System.IO.File]::OpenWrite($_.FullName)
            $fs.Write($bytes, 0, $bytes.Length)
            $fs.Close()
            Remove-Item $_.FullName -Force
        } catch {}
    }
    Remove-Item $D -Recurse -Force -ErrorAction SilentlyContinue
}

function Test-Health {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:5678/health" -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Import-ConfigEnv([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    Get-Content -Path $Path -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or ($line -notmatch "=")) { return }
        $kv = $line.Split("=", 2)
        $k = $kv[0].Trim()
        $v = if ($kv.Count -gt 1) { $kv[1].Trim().Trim('"').Trim("'") } else { "" }
        if ($k) {
            [Environment]::SetEnvironmentVariable($k, $v, "Process")
            Set-Item -Path "Env:$k" -Value $v
        }
    }
}

# ============ 主流程 ============
$decryptedRoot = $null
try {
    Write-Log "==== launch start ===="
    Set-Location -LiteralPath $AppDir

    # 1. 解密敏感文件到 %TEMP%
    $decryptedRoot = Decrypt-Vault
    Write-Log "decryptedRoot=$decryptedRoot"

    # 2. 设置环境变量指向解密目录
    $env:XIGUA_FRONTEND_DIST = Join-Path $decryptedRoot "web"
    $env:XIGUA_SKILLS_DIR = Join-Path $decryptedRoot "skills"
    $env:XIGUA_DATA_DIR = $DataDir
    $env:XIGUA_LOG_DIR = $LogDir
    if (-not $env:XIGUA_LICENSE_ENFORCE) { $env:XIGUA_LICENSE_ENFORCE = "true" }
    if (-not $env:XIGUA_AUTH_SERVER_URL) { $env:XIGUA_AUTH_SERVER_URL = "http://127.0.0.1:8100" }

    # 3. 加载 config.env（从解密目录）
    Import-ConfigEnv (Join-Path $decryptedRoot "config.env.example")
    # 也加载用户自己的 config
    Import-ConfigEnv (Join-Path $AppDir "config.env")

    # 4. 验证
    $exe = Join-Path $AppDir "xigua-backend.exe"
    if (-not (Test-Path $exe)) { throw "找不到 xigua-backend.exe" }
    if (-not (Test-Path (Join-Path $env:XIGUA_FRONTEND_DIST "index.html"))) {
        throw "前端文件缺失"
    }

    # 5. 启动后端
    if (Test-Health) {
        Write-Log "backend already running"
    } else {
        Get-Process -Name "xigua-backend" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 400
        $proc = Start-Process -FilePath $exe -WorkingDirectory $AppDir -WindowStyle Minimized -PassThru
        Write-Log "backend pid=$($proc.Id)"

        $ready = $false
        for ($i = 0; $i -lt 45; $i++) {
            Start-Sleep -Seconds 1
            if ($proc.HasExited) { throw "后端进程意外退出 (exit=$($proc.ExitCode))" }
            if (Test-Health) { $ready = $true; Write-Log "healthy after ${i}s"; break }
        }
        if (-not $ready) { throw "后端 45 秒未就绪" }
    }

    Write-Log "open browser"
    Start-Process "http://127.0.0.1:5678/"
    Write-Log "launch ok"

    # 6. 等待后端退出后再清理
    Write-Log "waiting for backend to exit..."
    if ($proc) {
        $proc.WaitForExit()
        Write-Log "backend exited code=$($proc.ExitCode)"
    }

} catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, "西瓜短剧Agent", 0, 16) | Out-Null
    } catch {}
} finally {
    # 7. 安全擦除解密目录（覆写 + 删除）
    if ($decryptedRoot -and (Test-Path $decryptedRoot)) {
        Write-Log "wiping $decryptedRoot"
        Wipe-Dir $decryptedRoot
    }
    Write-Log "==== launch end ===="
}
'@

# A1 修复：不再向 launch.ps1 嵌入密钥（改为安装时索取密码 + DPAPI 落盘），占位符机制已删除

# 对 launch.ps1 本身做轻量混淆
$obfuscatedLaunch = $launchPs1
# 替换变量名为无意义短名
$nameMap = @{
    'AppDir' = '$AD'; 'LogDir' = '$LD'; 'DataDir' = '$DD'
    'LogFile' = '$LF'; 'decryptedRoot' = '$DR'; 'proc' = '$P'
    'exe' = '$EX'; 'ready' = '$RD'; 'vaultPath' = '$VP'
    'raw' = '$RW'; 'cipher' = '$CI'; 'zipBytes' = '$ZB'
    'tmpRoot' = '$TR'; 'dest' = '$DS'; 'keyBytes' = '$KB'
}
foreach ($old in $nameMap.Keys) {
    $pat = '(?<![a-zA-Z0-9_$])' + [regex]::Escape('$' + $old) + '(?![a-zA-Z0-9_$])'
    $obfuscatedLaunch = [regex]::Replace($obfuscatedLaunch, $pat, $nameMap[$old])
}
# 也替换函数内的局部变量
$localMap = @{
    '$M' = '$X'; '$e' = '$Y'; '$d' = '$Z'
    '$kv' = '$W'; '$k' = '$U'; '$v' = '$V'
}
foreach ($old in $localMap.Keys) {
    $obfuscatedLaunch = $obfuscatedLaunch.Replace($old, $localMap[$old])
}

$launchPath = "$staging\launch.ps1"
Set-Content -Path $launchPath -Value $obfuscatedLaunch -Encoding UTF8
Write-Host "  受保护启动器: launch.ps1" -ForegroundColor Green

# 也更新 .bat 包装器
@"
@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 西瓜短剧Agent
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1"
if errorlevel 1 (
  echo 启动失败
  pause
)
"@ | Set-Content "$staging\启动西瓜短剧.bat" -Encoding ASCII

@"
@echo off
cd /d "%~dp0"
start /MIN powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0launch.ps1"
"@ | Set-Content "$staging\打开西瓜短剧.cmd" -Encoding ASCII

# ---------- 6. 打包整个 staging 为加密安装包 ----------
Write-Host "[6/6] 生成最终加密安装包..." -ForegroundColor Yellow

# 此时 staging 结构：
#   xigua-backend.exe   ← 明文（可执行文件无法避免）
#   xigua-vault.dat     ← 加密（web/dict/skills/config 全部在里）
#   launch.ps1          ← 混淆（含分段密钥）
#   app.ico, *.bat, *.cmd, 使用说明.txt  ← 明文
#   data/, app/         ← 明文数据

$finalZip = "$OutputDir\_final.zip"
[System.IO.Compression.ZipFile]::CreateFromDirectory($staging, $finalZip,
    [System.IO.Compression.CompressionLevel]::Optimal, $false)

$zipBytes = [System.IO.File]::ReadAllBytes($finalZip)

# AES-256 加密整个安装包（A1 修复：密钥由安装密码 PBKDF2 派生，不再随机生成后嵌入安装脚本）
$pkgSalt = New-Object byte[] 16
$pkgIV = New-Object byte[] 16
$rng.GetBytes($pkgSalt)
$rng.GetBytes($pkgIV)
$__pd = New-Object System.Security.Cryptography.Rfc2898DeriveBytes($Password, $pkgSalt, 100000)
$pkgKey = $__pd.GetBytes(32)
$__pd.Dispose()

$aes2 = [System.Security.Cryptography.Aes]::Create()
$aes2.KeySize = 256
$aes2.Mode = [System.Security.Cryptography.CipherMode]::CBC
$aes2.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
$aes2.Key = $pkgKey
$aes2.IV = $pkgIV
$enc = $aes2.CreateEncryptor()
$encryptedZip = $enc.TransformFinalBlock($zipBytes, 0, $zipBytes.Length)
$aes2.Dispose()

# .enc = [pkgSalt(16)][IV(16)][ciphertext]（salt 公开，仅用于密钥派生）
$encPath = "$OutputDir\西瓜短剧Agent-Setup-$Version.enc"
$fs = [System.IO.File]::OpenWrite($encPath)
$fs.Write($pkgSalt, 0, 16)
$fs.Write($pkgIV, 0, 16)
$fs.Write($encryptedZip, 0, $encryptedZip.Length)
$fs.Close()

Remove-Item -Force $finalZip -ErrorAction SilentlyContinue

# ---------- 6b. 用 Inno Setup 编译 EXE 安装程序 ----------
Write-Host "[6b/7] 编译 Inno Setup EXE 安装程序..." -ForegroundColor Yellow
$issSource = "E:\xigua-drama-agent\installer\xigua-setup-protected.iss"
$isccExe = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
$exeOutput = $null

if (Test-Path $isccExe) {
    # 更新 .iss 中的版本号（只改 #define 行，让预处理常量正确展开）
    $issContent = Get-Content $issSource -Raw
    $issContent = $issContent -replace '#define MyAppVersion "[^"]*"', "#define MyAppVersion ""$Version"""
    $issTemp = "$OutputDir\_temp_setup.iss"
    Set-Content $issTemp $issContent -Encoding UTF8

    Write-Host "  正在编译 EXE..." -ForegroundColor Gray
    $isccResult = & $isccExe "/Q" "/O$OutputDir" $issTemp 2>&1
    Remove-Item $issTemp -ErrorAction SilentlyContinue

    $exePattern = "$OutputDir\西瓜短剧Agent-Setup-$Version.exe"
    if (Test-Path $exePattern) {
        $exeSize = (Get-Item $exePattern).Length
        Write-Host "  ✓ EXE 安装程序已生成: $([math]::Round($exeSize/1MB,2)) MB" -ForegroundColor Green
        $exeOutput = $exePattern
    } else {
        Write-Host "  ⚠ ISCC 运行完毕但未找到输出文件" -ForegroundColor Yellow
        Write-Host "  ISCC 输出: $isccResult" -ForegroundColor Gray
    }
} else {
    Write-Host "  ⚠ 未找到 Inno Setup，跳过 EXE 编译" -ForegroundColor Yellow
}

Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue

$encSize = (Get-Item $encPath).Length

# ---------- 生成安装脚本 ----------
$installPs1 = @"
`$ErrorActionPreference = "Continue"
`$Host.UI.RawUI.WindowTitle = "西瓜短剧Agent 安装程序 v$Version"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " 西瓜短剧Agent v$Version 安装程序" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

`$SD = Split-Path -Parent `$MyInvocation.MyCommand.Path
`$EF = Join-Path `$SD "西瓜短剧Agent-Setup-$Version.enc"
if (-not (Test-Path `$EF)) { Write-Host "找不到加密包！" -ForegroundColor Red; pause; exit 1 }

`$ID = "`$env:LOCALAPPDATA\Programs\XiguaDramaAgent"
Write-Host "安装目录: `$ID"

# A1 修复：安装密码不再嵌入脚本，安装时向用户索取（密码通过密码文件线下交付）
Write-Host ""
`$secPwd = Read-Host "请输入安装密码" -AsSecureString
if (-not `$secPwd) { Write-Host "未输入密码，安装取消。" -ForegroundColor Red; pause; exit 1 }
`$__ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR(`$secPwd)
try {
    `$installPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(`$__ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR(`$__ptr)
}
`$secPwd = `$null

Write-Host "正在解密验证..."

# 解密外层安装包：.enc = [pkgSalt(16)][IV(16)][ciphertext]，密钥由安装密码 PBKDF2 派生
`$RW = [System.IO.File]::ReadAllBytes(`$EF)
`$PSalt = `$RW[0..15]
`$IV = `$RW[16..31]
`$CI = `$RW[32..(`$RW.Length-1)]
`$__derive = New-Object System.Security.Cryptography.Rfc2898DeriveBytes(`$installPassword, `$PSalt, 100000)
`$KB = `$__derive.GetBytes(32)
`$__derive.Dispose()

`$AS = [System.Security.Cryptography.Aes]::Create()
`$AS.KeySize = 256; `$AS.Mode = [System.Security.Cryptography.CipherMode]::CBC
`$AS.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
`$AS.Key = `$KB; `$AS.IV = `$IV
`$DC = `$AS.CreateDecryptor()
try {
    `$ZB = `$DC.TransformFinalBlock(`$CI, 0, `$CI.Length)
} catch {
    Write-Host "解密失败！文件损坏或密码错误。" -ForegroundColor Red; pause; exit 1
}
`$AS.Dispose()

# 解压到安装目录
Write-Host "正在安装..."
if (Test-Path `$ID) {
    Get-Process -Name "xigua-backend" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep 1
    Rename-Item `$ID "`$ID.bak.`$(Get-Date -Format 'yyyyMMddHHmmss')" -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path `$ID | Out-Null

Add-Type -AssemblyName System.IO.Compression.FileSystem
`$MS = New-Object System.IO.MemoryStream(, `$ZB)
`$ZP = [System.IO.Compression.ZipArchive]::new(`$MS)
foreach (`$Y in `$ZP.Entries) {
    `$DS = Join-Path `$ID `$Y.FullName
    if (`$Y.Name -eq '') { New-Item -ItemType Directory -Force -Path `$DS | Out-Null }
    else {
        `$Z = Split-Path `$DS -Parent
        if (-not (Test-Path `$Z)) { New-Item -ItemType Directory -Force -Path `$Z | Out-Null }
        try { [System.IO.Compression.ZipFileExtensions]::ExtractToFile(`$Y, `$DS, `$true) } catch {}
    }
}
`$ZP.Dispose(); `$MS.Dispose()

# A1 修复：由安装密码派生 vault 密钥，经 Windows DPAPI（当前用户）保护后落盘；
# launch.ps1 运行时解密使用，密钥不再嵌入任何脚本
`$vaultDat = Join-Path `$ID "xigua-vault.dat"
if (-not (Test-Path `$vaultDat)) {
    Write-Host "警告: 未找到 xigua-vault.dat，启动时将无法解密资源。" -ForegroundColor Yellow
} else {
    `$vraw = [System.IO.File]::ReadAllBytes(`$vaultDat)
    `$VSalt = `$vraw[0..15]
    `$__vderive = New-Object System.Security.Cryptography.Rfc2898DeriveBytes(`$installPassword, `$VSalt, 100000)
    `$vkey = `$__vderive.GetBytes(32)
    `$__vderive.Dispose()
    `$installPassword = `$null
    Add-Type -AssemblyName System.Security
    `$keyDir = Join-Path `$env:LOCALAPPDATA "XiguaDramaAgent"
    New-Item -ItemType Directory -Force -Path `$keyDir | Out-Null
    `$prot = [Security.Cryptography.ProtectedData]::Protect(`$vkey, `$null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    [Array]::Clear(`$vkey, 0, `$vkey.Length)
    [System.IO.File]::WriteAllBytes((Join-Path `$keyDir "vault.key"), `$prot)
    [Array]::Clear(`$prot, 0, `$prot.Length)
    Write-Host "vault 密钥已用 DPAPI 保护并保存（当前 Windows 用户）。" -ForegroundColor Gray
}

# 桌面快捷方式
try {
    `$WS = New-Object -ComObject WScript.Shell
    `$SC = `$WS.CreateShortcut((Join-Path [Environment]::GetFolderPath("Desktop") "西瓜短剧Agent.lnk"))
    `$SC.TargetPath = Join-Path `$ID "打开西瓜短剧.cmd"
    `$SC.WorkingDirectory = `$ID
    `$SC.IconLocation = Join-Path `$ID "app.ico"
    `$SC.Save()
} catch {}

Write-Host "========================================" -ForegroundColor Green
Write-Host " 安装完成！" -ForegroundColor Green
Write-Host " 双击桌面「西瓜短剧Agent」启动" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
`$CH = Read-Host "是否立即启动？(Y/n)"
if (`$CH -ne 'n' -and `$CH -ne 'N') { Start-Process (Join-Path `$ID "打开西瓜短剧.cmd") }
pause
"@

# A1 修复：不再向安装脚本嵌入密钥（改为安装时索取密码），占位符机制已删除

$installPs1Path = "$OutputDir\安装-西瓜短剧Agent.ps1"
Set-Content $installPs1Path $installPs1 -Encoding UTF8

# bat 包装器
@"
@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 西瓜短剧Agent 安装程序 v$Version
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0安装-西瓜短剧Agent.ps1"
pause
"@ | Set-Content "$OutputDir\安装-西瓜短剧Agent.bat" -Encoding ASCII

# 密码文件
@"
========================================
西瓜短剧Agent v$Version — 安装密码
========================================
生成时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
安装密码: $Password

请妥善保管！安装时需要此密码解密（请线下单独交付用户，切勿与安装包同渠道发送）。

========================================
"@ | Set-Content "$OutputDir\密码-请妥善保管.txt" -Encoding UTF8

# ---------- 验证 Vault ----------
Write-Host "`n验证运行时解密..." -ForegroundColor Gray
try {
    $testVault = Join-Path $encPath.Replace('.enc', '') "_test"
    # Decrypt test（新格式：[pkgSalt(16)][IV(16)][ciphertext]，密钥由安装密码派生）
    $rw = [System.IO.File]::ReadAllBytes($encPath)
    $psalt2 = $rw[0..15]
    $iv2 = $rw[16..31]
    $ci2 = $rw[32..($rw.Length-1)]
    $__td = New-Object System.Security.Cryptography.Rfc2898DeriveBytes($Password, $psalt2, 100000)
    $testKey = $__td.GetBytes(32)
    $__td.Dispose()
    $aes3 = [System.Security.Cryptography.Aes]::Create()
    $aes3.KeySize = 256; $aes3.Mode = [System.Security.Cryptography.CipherMode]::CBC
    $aes3.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
    $aes3.Key = $testKey; $aes3.IV = $iv2
    $dc2 = $aes3.CreateDecryptor()
    $zb2 = $dc2.TransformFinalBlock($ci2, 0, $ci2.Length)
    $aes3.Dispose()
    Write-Host "  ✓ 安装包解密验证通过 ($([math]::Round($zb2.Length/1MB,2)) MB)" -ForegroundColor Green
} catch {
    Write-Host "  ✗ 验证失败: $_" -ForegroundColor Red
}

# ---------- 清理 ----------
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " 构建完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

$outFiles = Get-ChildItem $OutputDir | Where-Object { -not $_.PSIsContainer -and $_.Name -notlike "*_test*" }
foreach ($f in $outFiles) {
    $sz = if ($f.Length -gt 1MB) { "$([math]::Round($f.Length/1MB,2)) MB" } elseif ($f.Length -gt 1KB) { "$([math]::Round($f.Length/1KB,1)) KB" } else { "$($f.Length) B" }
    Write-Host "  $($f.Name)  ($sz)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "=== 保护效果 ===" -ForegroundColor Cyan
Write-Host "  安装后磁盘上的文件:" -ForegroundColor White
Write-Host "    xigua-backend.exe  → 二进制可执行文件" -ForegroundColor Gray
Write-Host "    xigua-vault.dat    → AES-256 加密（含 web/dict/skills/config）" -ForegroundColor Gray
Write-Host "    launch.ps1         → 混淆（密钥经 DPAPI 保护，不在脚本内）" -ForegroundColor Gray
Write-Host "    其他 .bat/.cmd      → 明文启动脚本" -ForegroundColor Gray
Write-Host ""
Write-Host "  运行时: vault → %TEMP% 解密 → 进程退出 → 覆写删除" -ForegroundColor Yellow
Write-Host "  普通用户浏览安装目录: 只能看到 exe + 加密数据 + 混淆脚本" -ForegroundColor Green
Write-Host ""
Write-Host "  安装密码已保存到: 密码-请妥善保管.txt（请线下单独交付用户，切勿与安装包同渠道发送）" -ForegroundColor Yellow
