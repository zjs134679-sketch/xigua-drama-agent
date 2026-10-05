# =============================================================================
# 西瓜短剧Agent — 受保护安装包构建脚本 v2
# 前端/配置/词典 → 加密存储，运行时解密到 %TEMP%，退出即销毁
# =============================================================================
param(
    [string]$OutputDir = "E:\xigua-drama-agent\anzhuangbao",
    [string]$ReleaseDir = "E:\xigua-drama-agent\release",
    [string]$Version = "0.1.2"
)

$ErrorActionPreference = "Stop"

# ---------- 密码生成 ----------
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$keyBytes = New-Object byte[] 32
$ivBytes = New-Object byte[] 16
$rng.GetBytes($keyBytes)
$rng.GetBytes($ivBytes)
$installPassword = ($keyBytes | ForEach-Object { $_.ToString("x2") }) -join ""

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " 西瓜短剧Agent — 受保护安装包构建 v2" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

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

# vault.dat = [IV(16)][ciphertext]
$vaultPath = "$staging\xigua-vault.dat"
$fs = [System.IO.File]::OpenWrite($vaultPath)
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

# 将密钥拆分为 8 段，Base64 编码，启动时动态拼接
$keyStr = [Convert]::ToBase64String($keyBytes)
$parts = @()
$splitSize = [math]::Ceiling($keyStr.Length / 8)
for ($i = 0; $i -lt 8; $i++) {
    $chunk = $keyStr.Substring($i * $splitSize, [math]::Min($splitSize, $keyStr.Length - $i * $splitSize))
    $parts += $chunk
}
# 打乱顺序
$order = @(3, 6, 1, 7, 0, 5, 2, 4)  # 固定排列
$shuffled = @(0..7)
for ($i = 0; $i -lt 8; $i++) { $shuffled[$i] = $parts[$order[$i]] }

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
    # === 密钥重组（分段混淆）===
    $k0 = 'PLACEHOLDER_K0'
    $k1 = 'PLACEHOLDER_K1'
    $k2 = 'PLACEHOLDER_K2'
    $k3 = 'PLACEHOLDER_K3'
    $k4 = 'PLACEHOLDER_K4'
    $k5 = 'PLACEHOLDER_K5'
    $k6 = 'PLACEHOLDER_K6'
    $k7 = 'PLACEHOLDER_K7'
    # 按正确顺序拼接
    $joined = $k0 + $k1 + $k2 + $k3 + $k4 + $k5 + $k6 + $k7
    $keyBytes = [Convert]::FromBase64String($joined)

    $vaultPath = Join-Path $AppDir "xigua-vault.dat"
    if (-not (Test-Path $vaultPath)) {
        throw "找不到加密数据文件 xigua-vault.dat"
    }

    $raw = [System.IO.File]::ReadAllBytes($vaultPath)
    $iv = $raw[0..15]
    $cipher = $raw[16..($raw.Length - 1)]

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

# 将密钥拆为 4 段嵌入启动器
$keyB64 = [Convert]::ToBase64String($keyBytes)
# 确保每段长度一致（Base64 不含 = 的纯字符部分）
$keyClean = $keyB64.TrimEnd('=')
$chunkLen = [math]::Floor($keyClean.Length / 4)
$kp = @()
$kp += $keyClean.Substring(0, $chunkLen)
$kp += $keyClean.Substring($chunkLen, $chunkLen)
$kp += $keyClean.Substring($chunkLen * 2, $chunkLen)
$kp += $keyClean.Substring($chunkLen * 3)  # 剩余全部（含尾部）

# 替换占位符
$launchPs1 = $launchPs1.Replace('PLACEHOLDER_K0', "'$($kp[0])'")
$launchPs1 = $launchPs1.Replace('PLACEHOLDER_K1', "'$($kp[1])'")
$launchPs1 = $launchPs1.Replace('PLACEHOLDER_K2', "'$($kp[2])'")
$launchPs1 = $launchPs1.Replace('PLACEHOLDER_K3', "'$($kp[3])'")
# k4-k7 填空串（不用）
$launchPs1 = $launchPs1.Replace('PLACEHOLDER_K4', "''")
$launchPs1 = $launchPs1.Replace('PLACEHOLDER_K5', "''")
$launchPs1 = $launchPs1.Replace('PLACEHOLDER_K6', "''")
$launchPs1 = $launchPs1.Replace('PLACEHOLDER_K7', "''")

# 更新 launcher 中的拼接为只用 4 段
$launchPs1 = $launchPs1.Replace('$k0 + $k1 + $k2 + $k3 + $k4 + $k5 + $k6 + $k7', '$k0 + $k1 + $k2 + $k3')

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

# AES-256 加密整个安装包
$pkgKey = New-Object byte[] 32
$pkgIV = New-Object byte[] 16
$rng.GetBytes($pkgKey)
$rng.GetBytes($pkgIV)
$pkgPassword = ($pkgKey | ForEach-Object { $_.ToString("x2") }) -join ""

$aes2 = [System.Security.Cryptography.Aes]::Create()
$aes2.KeySize = 256
$aes2.Mode = [System.Security.Cryptography.CipherMode]::CBC
$aes2.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
$aes2.Key = $pkgKey
$aes2.IV = $pkgIV
$enc = $aes2.CreateEncryptor()
$encryptedZip = $enc.TransformFinalBlock($zipBytes, 0, $zipBytes.Length)
$aes2.Dispose()

$encPath = "$OutputDir\西瓜短剧Agent-Setup-$Version.enc"
$fs = [System.IO.File]::OpenWrite($encPath)
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
Write-Host "正在解密验证..."

# 解密
`$RW = [System.IO.File]::ReadAllBytes(`$EF)
`$IV = `$RW[0..15]
`$CI = `$RW[16..(`$RW.Length-1)]
`$KB = [byte[]]@()  # 下面从占位符填充
$pkgKeyBytes_placeholder

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

# 在安装脚本中嵌入密钥（分段 + Base64）
$pkgKeyB64 = [Convert]::ToBase64String($pkgKey)
$pkgParts = @()
$sz = [math]::Ceiling($pkgKeyB64.Length / 4)
for ($i = 0; $i -lt 4; $i++) {
    $pkgParts += $pkgKeyB64.Substring($i * $sz, [math]::Min($sz, $pkgKeyB64.Length - $i * $sz))
}
$keyLine = "`$KB = [Convert]::FromBase64String('$($pkgParts[0])'+'$($pkgParts[1])'+'$($pkgParts[2])'+'$($pkgParts[3])')"
$installPs1 = $installPs1.Replace('$pkgKeyBytes_placeholder', $keyLine)

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
安装密码: $pkgPassword

请妥善保管！安装时需要此密码解密。

========================================
"@ | Set-Content "$OutputDir\密码-请妥善保管.txt" -Encoding UTF8

# ---------- 验证 Vault ----------
Write-Host "`n验证运行时解密..." -ForegroundColor Gray
try {
    $testVault = Join-Path $encPath.Replace('.enc', '') "_test"
    # Decrypt test
    $rw = [System.IO.File]::ReadAllBytes($encPath)
    $iv2 = $rw[0..15]
    $ci2 = $rw[16..($rw.Length-1)]
    $aes3 = [System.Security.Cryptography.Aes]::Create()
    $aes3.KeySize = 256; $aes3.Mode = [System.Security.Cryptography.CipherMode]::CBC
    $aes3.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
    $aes3.Key = $pkgKey; $aes3.IV = $iv2
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
Write-Host "    launch.ps1         → 混淆 + 分段密钥" -ForegroundColor Gray
Write-Host "    其他 .bat/.cmd      → 明文启动脚本" -ForegroundColor Gray
Write-Host ""
Write-Host "  运行时: vault → %TEMP% 解密 → 进程退出 → 覆写删除" -ForegroundColor Yellow
Write-Host "  普通用户浏览安装目录: 只能看到 exe + 加密数据 + 混淆脚本" -ForegroundColor Green
Write-Host ""
Write-Host "  安装密码: $pkgPassword" -ForegroundColor Yellow
Write-Host "  密码已保存到: 密码-请妥善保管.txt" -ForegroundColor Yellow
