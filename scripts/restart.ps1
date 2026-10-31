# 干净重启演示服务：先杀掉所有旧的 bankagent.api 进程，再启动一个，并等它健康
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$port = if ($env:BANK_PORT) { $env:BANK_PORT } else { "8000" }
$env:BANK_PORT = $port

Write-Host "清理旧服务进程 ..." -ForegroundColor DarkGray
$old = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
    Where-Object { $_.CommandLine -match "bankagent\.api" }
foreach ($p in $old) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

$left = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
    Where-Object { $_.CommandLine -match "bankagent\.api" })
if ($left.Count -gt 0) {
    Write-Host "仍有旧进程未退出，请手动结束：" -ForegroundColor Red
    $left | Select-Object ProcessId, CommandLine | Format-Table -AutoSize
    exit 1
}

Write-Host "启动服务 (端口 $port) ..." -ForegroundColor DarkGray
$proc = Start-Process -FilePath "python" -ArgumentList "-u", "-m", "bankagent.api" `
    -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden -PassThru

$ok = $false
for ($i = 0; $i -lt 25; $i++) {
    Start-Sleep -Milliseconds 800
    try {
        $h = Invoke-RestMethod "http://127.0.0.1:$port/health" -TimeoutSec 2
        if ($h.ok) { $ok = $true; break }
    } catch { }
}
if (-not $ok) {
    Write-Host "启动失败，请检查端口占用或看后台输出。" -ForegroundColor Red
    exit 1
}

Write-Host ("已启动：http://127.0.0.1:" + $port + "  (PID " + $proc.Id + ")") -ForegroundColor Green
Write-Host ("模型：enabled=" + $h.brain.enabled + " model=" + $h.brain.model)
