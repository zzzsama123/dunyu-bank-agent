# 便携版启动器：内置运行时 + 离线语音模型；端口被占用时自动顺延。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "runtime\python\python.exe"

if (-not (Test-Path $py)) {
    Write-Host ""
    Write-Host "  这是盾语的源码仓库，不是免安装运行包。" -ForegroundColor Yellow
    Write-Host "  仓库不包含 Python 运行时、Vosk 语音模型和 BGE 语义模型，不能直接双击运行。" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  评委请下载 Release 便携包：" -ForegroundColor Cyan
    Write-Host "  https://github.com/zzzsama123/dunyu-bank-agent/releases/tag/v1.0-demo" -ForegroundColor Cyan
    Write-Host "  解压后双击 start.bat 即可。" -ForegroundColor DarkGray
    Write-Host ""
    if ($env:BANK_SOURCE_RUN -ne "1") { exit 1 }
    $py = "python"
}

if ($py -ne "python") {
    $model = Join-Path $root "runtime\asr\model"
    if (Test-Path (Join-Path $model "am\final.mdl")) { $env:VOSK_MODEL = $model }
}
if (-not $env:BANK_OPEN_BROWSER) { $env:BANK_OPEN_BROWSER = "1" }

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

function Test-OurApp([int]$p) {
    try {
        $h = Invoke-RestMethod "http://127.0.0.1:$p/health" -TimeoutSec 2
        return [bool]($h.ok -and $h.version)
    } catch {
        return $false
    }
}

# 已经有盾语在跑：直接打开浏览器，不再起第二个端口
for ($p = 8000; $p -lt 8020; $p++) {
    if (Test-OurApp $p) {
        Write-Host ""
        Write-Host "  盾语已经在运行：http://127.0.0.1:$p" -ForegroundColor Cyan
        Write-Host "  已为你打开浏览器；关闭原来的启动窗口才会停止服务。" -ForegroundColor DarkGray
        if ($env:BANK_OPEN_BROWSER -ne "0") { Start-Process "http://127.0.0.1:$p" }
        exit 0
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