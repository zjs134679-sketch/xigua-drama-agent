$ErrorActionPreference = "SilentlyContinue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RunDir = Join-Path $Root ".run"
$stopped = @{}

foreach ($name in @("frontend", "backend", "auth")) {
    $pidFile = Join-Path $RunDir "$name.pid"
    if (Test-Path $pidFile) {
        $processId = [int](Get-Content -LiteralPath $pidFile -Raw)
        if ($processId -gt 0 -and -not $stopped.ContainsKey($processId)) {
            & taskkill.exe /PID $processId /T /F | Out-Null
            $stopped[$processId] = $true
        }
        Remove-Item -LiteralPath $pidFile -Force
    }
}

# Also clean older project processes created before PID tracking existed.
Get-CimInstance Win32_Process | Where-Object {
    ([string]$_.CommandLine).IndexOf($Root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
    ($_.CommandLine -match "uvicorn|vite")
} | ForEach-Object {
    if (-not $stopped.ContainsKey($_.ProcessId)) {
        & taskkill.exe /PID $_.ProcessId /T /F | Out-Null
    }
}

Write-Host "西瓜短剧Agent 服务已停止。" -ForegroundColor Green
