# 启动演示服务
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $root "backend")
try {
    python -m bankagent.api
} finally {
    Pop-Location
}
