# 打包提交物：生成 dist\bankagent-demo.zip
# 自动剔除 .env / 数据库 / 缓存 / 评测报告，并扫描是否误带真实 key
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root "dist"
$stage = Join-Path $dist "_stage"
$zip = Join-Path $dist "bankagent-demo.zip"

Write-Host "=== 打包参赛作品 ===" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $dist | Out-Null
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
if (Test-Path $zip) { Remove-Item $zip -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

foreach ($item in @("README.md", "docs", "backend", "frontend", "scripts", "start.bat", ".gitignore")) {
    $src = Join-Path $root $item
    if (Test-Path $src) { Copy-Item $src -Destination $stage -Recurse -Force }
}

# --- 清理敏感/临时文件 ---
Remove-Item (Join-Path $stage "backend\vendor") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $stage "backend\models") -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem $stage -Recurse -File -Include "*.pyc", "*.log" | Remove-Item -Force
Remove-Item (Join-Path $stage "backend\.env") -Force -ErrorAction SilentlyContinue
Get-ChildItem (Join-Path $stage "backend\data") -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "*.db*" } | Remove-Item -Force
Remove-Item (Join-Path $stage "backend\eval\report.json") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $stage "backend\tests\_*.py") -Force -ErrorAction SilentlyContinue

# --- 临时/探针文件扫描 ---
$probes = Get-ChildItem $stage -Recurse -File | Where-Object { $_.Name -match "^_(battery|coldstart|diag|probe|cat_dbg|tmp|test_out)" -or $_.Name -like "*.log" -or $_.Name -like "*.tsv" }
if ($probes) { Write-Host "[阻断] 发现临时/探针文件：" -ForegroundColor Red; $probes | Select-Object -First 5 | ForEach-Object { Write-Host ("  " + $_.FullName) }; exit 1 }

# --- 安全扫描：不能带真实 key ---
$textFiles = Get-ChildItem $stage -Recurse -File -Include "*.py", "*.md", "*.json", "*.js", "*.css",
    "*.html", "*.bat", "*.ps1", "*.example", "*.rego", "*.txt"
$hits = @()
foreach ($f in $textFiles) {
    $m = Select-String -Path $f.FullName -Pattern "sk-[A-Za-z0-9]{20,}" -ErrorAction SilentlyContinue
    if ($m) { $hits += $m }
}
if ($hits.Count -gt 0) {
    Write-Host "`n[阻断] 打包内容里发现疑似真实 Key：" -ForegroundColor Red
    $hits | ForEach-Object { Write-Host ("  " + $_.Path + ":" + $_.LineNumber) }
    exit 1
}

Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip -Force
Remove-Item $stage -Recurse -Force

$size = [math]::Round((Get-Item $zip).Length / 1MB, 2)
$count = ([System.IO.Compression.ZipFile]::OpenRead($zip)).Entries.Count
Write-Host "`n已生成：$zip" -ForegroundColor Green
Write-Host ("文件数 {0} / 大小 {1} MB" -f $count, $size)
Write-Host "`n提交前请确认：" -ForegroundColor Yellow
Write-Host "  1) 压缩包内没有 backend\.env、data\*.db（已自动剔除）"
Write-Host "  2) README 的“Key 与评审”一节已看过"
Write-Host "  3) 视频/PPT/截图里没有出现 Key"
