# 运行全部单元测试
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $root "backend")
try {
    python -m unittest discover tests -v
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
