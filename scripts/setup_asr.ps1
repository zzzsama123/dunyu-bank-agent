# 一键启用离线语音识别（可选）：
#   1) 把 vosk 装进 backend\vendor（不污染系统 Python）
#   2) 下载中文小模型到 %LOCALAPPDATA%\BankAgent（纯英文路径，Vosk 不认中文路径）
# 之后运行完全离线，不需要联网、不需要 Key。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$vendor = Join-Path $root "backend\vendor"
$modelDir = Join-Path $env:LOCALAPPDATA "BankAgent\vosk-model-small-cn-0.22"
$zip = Join-Path $env:TEMP "vosk-model-small-cn-0.22.zip"
$mirror = "https://pypi.tuna.tsinghua.edu.cn/simple"
$modelUrl = "https://hf-mirror.com/localstack/vosk-models/resolve/main/vosk-model-small-cn-0.22.zip"

Write-Host "=== 启用离线语音识别 ===" -ForegroundColor Cyan

if (-not (Test-Path (Join-Path $vendor "vosk"))) {
    Write-Host "安装 vosk 到 backend\vendor ..." -ForegroundColor DarkGray
    python -m pip install -i $mirror --target $vendor vosk
} else {
    Write-Host "vosk 已安装（backend\vendor）" -ForegroundColor DarkGray
}

if (-not (Test-Path (Join-Path $modelDir "am\final.mdl"))) {
    Write-Host "下载中文模型（约 44MB）..." -ForegroundColor DarkGray
    Invoke-WebRequest -Uri $modelUrl -OutFile $zip -UseBasicParsing
    $tmp = Join-Path $env:TEMP ("vosk_extract_" + [guid]::NewGuid().ToString("N"))
    Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force
    New-Item -ItemType Directory -Force -Path (Split-Path $modelDir -Parent) | Out-Null
    if (Test-Path $modelDir) { Remove-Item $modelDir -Recurse -Force }
    Move-Item (Join-Path $tmp "vosk-model-small-cn-0.22") $modelDir
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "模型已就绪：$modelDir" -ForegroundColor DarkGray
}

Write-Host "`n完成。重启服务后 /health 的 asr.engine 应为 vosk-small-cn。" -ForegroundColor Green
Write-Host "如需卸载：删除 backend\vendor 与 $modelDir 即可。" -ForegroundColor DarkGray
