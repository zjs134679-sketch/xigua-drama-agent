$ErrorActionPreference="Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$out = Join-Path $repoRoot "anzhuangbao"
$rel = Join-Path $repoRoot "release"
$ver="0.1.2"

Write-Host "=== 西瓜短剧Agent 安装包构建 ===" -ForegroundColor Cyan

# 1. Prepare staging
$stg="$out\staging"
Get-ChildItem $stg -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $stg | Out-Null
Get-ChildItem $rel | ForEach-Object { Copy-Item -Recurse -Force $_.FullName $stg }
Write-Host "[1/4] Staging copied"

# 2. Clean sensitive data
Remove-Item -Force "$stg\dict\*.local.txt" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$stg\logs" -ErrorAction SilentlyContinue
$txt = [System.IO.File]::ReadAllText("$stg\使用说明.txt", [System.Text.Encoding]::UTF8)
$txt = $txt -replace 'E:\\xigua Agent  密码管理\\一键启动\.bat', '授权服务器(auth-server)'
[System.IO.File]::WriteAllText("$stg\使用说明.txt", $txt, [System.Text.Encoding]::UTF8)

# 3. Generate keys & build encrypted vault
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$kb = New-Object byte[] 32; $ivb = New-Object byte[] 16
$rng.GetBytes($kb); $rng.GetBytes($ivb)

$vtmp = "$out\_vtmp"
New-Item -ItemType Directory -Force -Path $vtmp | Out-Null
@('web','dict','skills') | ForEach-Object {
    $src = "$stg\$_"
    if (Test-Path $src) {
        Get-ChildItem $src -Recurse -File | ForEach-Object {
            $relPath = $_.FullName.Substring($stg.Length + 1)
            $dest = "$vtmp\$relPath"
            $parent = Split-Path $dest -Parent
            if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
            Copy-Item $_.FullName $dest
        }
    }
}
if (Test-Path "$stg\config.env.example") { Copy-Item "$stg\config.env.example" $vtmp }

Add-Type -AssemblyName System.IO.Compression.FileSystem
$vz = "$out\_v.zip"
[System.IO.Compression.ZipFile]::CreateFromDirectory($vtmp, $vz, [System.IO.Compression.CompressionLevel]::Optimal, $false)
$zb = [System.IO.File]::ReadAllBytes($vz)

$aes = [System.Security.Cryptography.Aes]::Create()
$aes.KeySize = 256; $aes.Mode = [System.Security.Cryptography.CipherMode]::CBC
$aes.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
$aes.Key = $kb; $aes.IV = $ivb
$enc = $aes.CreateEncryptor().TransformFinalBlock($zb, 0, $zb.Length)
$aes.Dispose()

$vp = "$stg\xigua-vault.dat"
$fs = [System.IO.File]::OpenWrite($vp)
$fs.Write($ivb, 0, 16)
$fs.Write($enc, 0, $enc.Length)
$fs.Close()
Remove-Item -Recurse -Force $vtmp, $vz -ErrorAction SilentlyContinue
@('web','dict','skills') | ForEach-Object { Remove-Item -Recurse -Force "$stg\$_" -ErrorAction SilentlyContinue }
Remove-Item -Force "$stg\config.env.example" -ErrorAction SilentlyContinue
Write-Host "[2/4] Vault created ($([math]::Round((Get-Item $vp).Length/1MB,2)) MB)"

# 4. Generate protected launcher with embedded split key
$keyB64 = [Convert]::ToBase64String($kb)
$L = [math]::Floor($keyB64.Length / 4)
$k0 = $keyB64.Substring(0, $L)
$k1 = $keyB64.Substring($L, $L)
$k2 = $keyB64.Substring(2 * $L, $L)
$k3 = $keyB64.Substring(3 * $L)

$lp = @'
$ErrorActionPreference="Continue"
$AD=Split-Path -Parent $MyInvocation.MyCommand.Path;if(!$AD){$AD=(Get-Location).Path}
$LD="$env:LOCALAPPDATA\XiguaDramaAgent\logs";$DD="$env:LOCALAPPDATA\XiguaDramaAgent\data"
New-Item -ItemType Directory -Force -Path $LD,$DD|Out-Null
$LF=Join-Path $LD "launcher.log"
function wl($X){try{Add-Content $LF ("{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"),$X) -Encoding UTF8}catch{}}
function chk{try{$r=iwr "http://127.0.0.1:5678/health" -UseBasicParsing -TimeoutSec 2;return $r.StatusCode -eq 200}catch{return $false}}
function clr($D){if(!$D -or !(Test-Path $D)){return}
  Get-ChildItem $D -Recurse -File|ForEach-Object{try{$b=New-Object byte[] 4096;$f=[System.IO.File]::OpenWrite($_.FullName);$f.Write($b,0,$b.Length);$f.Close();Remove-Item $_.FullName -Force}catch{}}
  Remove-Item $D -Recurse -Force -ErrorAction SilentlyContinue}
try{
  wl "start"
  Set-Location $AD
  $VP=Join-Path $AD "xigua-vault.dat"
  if(!(Test-Path $VP)){throw "xigua-vault.dat missing"}
  $RW=[System.IO.File]::ReadAllBytes($VP);$IV=$RW[0..15];$CI=$RW[16..($RW.Length-1)]
  $KB=[Convert]::FromBase64String('__K0__'+'__K1__'+'__K2__'+'__K3__')
  $AS=[System.Security.Cryptography.Aes]::Create();$AS.KeySize=256;$AS.Mode=[System.Security.Cryptography.CipherMode]::CBC;$AS.Padding=[System.Security.Cryptography.PaddingMode]::PKCS7;$AS.Key=$KB;$AS.IV=$IV
  $ZB=$AS.CreateDecryptor().TransformFinalBlock($CI,0,$CI.Length);$AS.Dispose()
  $TR=Join-Path $env:TEMP ("_x"+[System.IO.Path]::GetRandomFileName())
  New-Item -ItemType Directory -Force -Path $TR|Out-Null
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $MS=New-Object System.IO.MemoryStream(,$ZB);$ZP=[System.IO.Compression.ZipArchive]::new($MS)
  foreach($Y in $ZP.Entries){$DS=Join-Path $TR $Y.FullName
    if($Y.Name -eq ''){New-Item -ItemType Directory -Force -Path $DS|Out-Null}
    else{$Z=Split-Path $DS -Parent;if(!(Test-Path $Z)){New-Item -ItemType Directory -Force -Path $Z|Out-Null}
      try{[System.IO.Compression.ZipFileExtensions]::ExtractToFile($Y,$DS,$true)}catch{}}}
  $ZP.Dispose();$MS.Dispose()
  wl "decrypted"
  $env:XIGUA_FRONTEND_DIST=Join-Path $TR "web"
  $env:XIGUA_SKILLS_DIR=Join-Path $TR "skills"
  $env:XIGUA_DATA_DIR=$DD;$env:XIGUA_LOG_DIR=$LD
  if(!$env:XIGUA_LICENSE_ENFORCE){$env:XIGUA_LICENSE_ENFORCE="true"}
  if(!$env:XIGUA_AUTH_SERVER_URL){$env:XIGUA_AUTH_SERVER_URL="http://127.0.0.1:8100"}
  $ce=Join-Path $TR "config.env.example"
  if(Test-Path $ce){Get-Content $ce -Encoding UTF8|ForEach-Object{$l=$_.Trim();if($l -and !$l.StartsWith("#") -and $l.Contains("=")){$kv=$l.Split("=",2);$k=$kv[0].Trim();$v=$kv[1].Trim().Trim('"').Trim("'");if($k){[Environment]::SetEnvironmentVariable($k,$v,"Process");Set-Item "Env:$k" $v}}}}
  $cu=Join-Path $AD "config.env"
  if(Test-Path $cu){Get-Content $cu -Encoding UTF8|ForEach-Object{$l=$_.Trim();if($l -and !$l.StartsWith("#") -and $l.Contains("=")){$kv=$l.Split("=",2);$k=$kv[0].Trim();$v=$kv[1].Trim().Trim('"').Trim("'");if($k){[Environment]::SetEnvironmentVariable($k,$v,"Process");Set-Item "Env:$k" $v}}}}
  $EX=Join-Path $AD "xigua-backend.exe"
  if(!(Test-Path $EX)){throw "xigua-backend.exe not found"}
  if(!(Test-Path (Join-Path $env:XIGUA_FRONTEND_DIST "index.html"))){throw "frontend missing"}
  if(chk){wl "already running"}else{
    Get-Process "xigua-backend" -ErrorAction SilentlyContinue|Stop-Process -Force -ErrorAction SilentlyContinue;Start-Sleep -Milliseconds 400
    $P=Start-Process $EX -WorkingDirectory $AD -WindowStyle Minimized -PassThru;wl "pid=$($P.Id)"
    $RD=$false;for($i=0;$i -lt 45;$i++){Start-Sleep 1;if($P.HasExited){throw "backend crashed"};if(chk){$RD=$true;break}}
    if(!$RD){throw "backend timeout"}}
  wl "open";Start-Process "http://127.0.0.1:5678/";wl "ok"
  if($P){$P.WaitForExit();wl "exit=$($P.ExitCode)"}
}catch{wl "ERR: $($_.Exception.Message)";try{Add-Type -AssemblyName System.Windows.Forms;[System.Windows.Forms.MessageBox]::Show($_.Exception.Message,"西瓜短剧Agent",0,16)|Out-Null}catch{}}
finally{if($TR -and (Test-Path $TR)){wl "wipe";clr $TR};wl "end"}
'@
$lp = $lp.Replace('__K0__', $k0).Replace('__K1__', $k1).Replace('__K2__', $k2).Replace('__K3__', $k3)
Set-Content "$stg\launch.ps1" $lp -Encoding UTF8

@"
@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1"
if errorlevel 1 pause
"@ | Set-Content "$stg\启动西瓜短剧.bat" -Encoding ASCII

@"
@echo off
cd /d "%~dp0"
start /MIN powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0launch.ps1"
"@ | Set-Content "$stg\打开西瓜短剧.cmd" -Encoding ASCII

Write-Host "[3/4] Launcher generated"

# 5. Build Inno Setup EXE
Write-Host "[4/4] Compiling EXE..."
$issSrc = Join-Path $repoRoot "installer\xigua-setup-protected.iss"
$issContent = (Get-Content $issSrc -Raw) -replace '#define MyAppVersion "[^"]*"', "#define MyAppVersion ""$ver"""
$issTmp = "$out\_setup.iss"
Set-Content $issTmp $issContent -Encoding UTF8
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "/Q" "/O$out" $issTmp
Remove-Item $issTmp -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $stg -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "=== DONE ===" -ForegroundColor Green
Get-ChildItem $out -File | ForEach-Object {
    $sz = if($_.Length -gt 1MB){"$([math]::Round($_.Length/1MB,2)) MB"}else{"$($_.Length) B"}
    Write-Host "  $($_.Name) ($sz)" -ForegroundColor White
}
