# 便携版启动器：内置运行时 + 离线语音模型；端口被占用时自动顺延。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "runtime\python\python.exe"

if (Test-Path $py) {
    $model = Join-Path $root "runtime\asr\model"
    if (Test-Path (Join-Path $model "am\final.mdl")) { $env:VOSK_MODEL = $model }
    if (-not $env:BANK_OPEN_BROWSER) { $env:BANK_OPEN_BROWSER = "1" }
} else {
    $py = "python"
    if (-not $env:BANK_OPEN_BROWSER) { $env:BANK_OPEN_BROWSER = "1" }
}

$backend = Join-Path $root "backend"
Set-Location $backend
if (-not (Test-Path "data")) { New-Item -ItemType Directory -Path "data" | Out-Null }

function Test-Port([int]$p) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect("127.0.0.1", $p)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

$port = if ($env:BANK_PORT) { [int]$env:BANK_PORT } else { 8000 }
for ($i = 0; $i -lt 20; $i++) {
    if (-not (Test-Port $port)) { break }
    $port++
}
$env:BANK_PORT = "$port"

Write-Host ""
Write-Host "  盾语 · 银行 AI 智能体（仿真环境）正在启动…" -ForegroundColor Cyan
if ($port -ne 8000) {
    Write-Host "  8000 已被占用，本次使用 http://127.0.0.1:$port" -ForegroundColor Yellow
} else {
    Write-Host "  浏览器会自动打开 http://127.0.0.1:$port" -ForegroundColor DarkGray
}
Write-Host "  关闭本窗口即停止服务。" -ForegroundColor DarkGray
Write-Host ""
& $py -m bankagent.api