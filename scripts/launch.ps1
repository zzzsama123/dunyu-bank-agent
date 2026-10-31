# 便携版启动器：设置内置运行时与离线语音模型，再启动服务。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "runtime\python\python.exe"

if (Test-Path $py) {
    $cache = Join-Path $env:PUBLIC "BankAgent\vosk-model-small-cn-0.22"
    if (-not (Test-Path (Join-Path $cache "am\final.mdl"))) {
        Write-Host "首次启动：正在准备离线语音模型（约 65MB，几秒钟）..." -ForegroundColor DarkGray
        $parent = Split-Path $cache -Parent
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
        Copy-Item (Join-Path $root "runtime\asr\model") $cache -Recurse -Force
    }
    $env:VOSK_MODEL = $cache
    if (-not $env:BANK_OPEN_BROWSER) { $env:BANK_OPEN_BROWSER = "1" }
} else {
    $py = "python"
    if (-not $env:BANK_OPEN_BROWSER) { $env:BANK_OPEN_BROWSER = "1" }
}

$backend = Join-Path $root "backend"
Set-Location $backend
if (-not (Test-Path "data")) { New-Item -ItemType Directory -Path "data" | Out-Null }

Write-Host ""
Write-Host "  银行 AI 智能体（仿真）正在启动…" -ForegroundColor Cyan
Write-Host "  浏览器会自动打开 http://127.0.0.1:8000" -ForegroundColor DarkGray
Write-Host "  关闭本窗口即停止服务。" -ForegroundColor DarkGray
Write-Host ""
& $py -m bankagent.api
