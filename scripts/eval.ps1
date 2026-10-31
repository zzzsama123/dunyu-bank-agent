# 运行评测（权限边界 40 / 意图 60 / 红队 40 / 端到端 12）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $root "backend")
try {
    python -m eval.runner
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
