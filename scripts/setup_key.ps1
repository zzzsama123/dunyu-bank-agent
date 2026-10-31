# 一次性配置大模型 Key（写入本机 bankagent\backend\.env，不会进仓库、不会出现在打包里）
# 用法：右键“使用 PowerShell 运行”，或在终端执行  powershell -ExecutionPolicy Bypass -File scripts\setup_key.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root "backend\.env"

Write-Host "=== 银行 AI 智能体 · 大模型 Key 配置 ===" -ForegroundColor Cyan
Write-Host "Key 只会写到本机：$envPath"
Write-Host "（.env 已在 .gitignore 中，打包脚本也会剔除）`n"

$secure = Read-Host "粘贴 LLM_API_KEY（输入过程不可见，不会回显）" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
if ([string]::IsNullOrWhiteSpace($key) -or $key.Length -lt 8) {
    Write-Host "Key 为空或长度异常，已取消。" -ForegroundColor Red
    exit 1
}

$model = Read-Host "模型名（回车默认 deepseek-chat）"
if ([string]::IsNullOrWhiteSpace($model)) { $model = "deepseek-chat" }
$base = Read-Host "Base URL（回车默认 https://api.deepseek.com/v1）"
if ([string]::IsNullOrWhiteSpace($base)) { $base = "https://api.deepseek.com/v1" }

# 保留 .env 里已有的其它配置，只更新这三项
$lines = @()
if (Test-Path $envPath) {
    $lines = @(Get-Content $envPath -Encoding UTF8 |
        Where-Object { $_ -notmatch '^\s*(LLM_API_KEY|LLM_MODEL|LLM_BASE_URL)\s*=' })
}
$lines += "LLM_API_KEY=$key"
$lines += "LLM_MODEL=$model"
$lines += "LLM_BASE_URL=$base"
[System.IO.File]::WriteAllLines($envPath, $lines, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "`n已写入配置（Key 不回显）。" -ForegroundColor Green

$check = Read-Host "现在做一次连通性自检？(y/N)"
if ($check -match '^[Yy]') {
    Push-Location (Join-Path $root "backend")
    try { python -m bankagent.llm } finally { Pop-Location }
}

Write-Host "`n以后直接双击 start.bat 即可，不需要再配。" -ForegroundColor Cyan
Write-Host "想改/删除 Key：直接编辑或删除 backend\.env。" -ForegroundColor DarkGray
