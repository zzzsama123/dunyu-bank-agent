# 便携包白名单打包：只允许交付目录进 zip，根目录探针/临时文件一律不进。
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$root = Split-Path -Parent $PSScriptRoot
$build = Join-Path $root "portable_build"
$dist = Join-Path $root "dist"
$stage = Join-Path $dist "_portable_stage"
$zip = Join-Path $dist "bankagent-portable.zip"

Write-Host "=== 便携包白名单打包 ===" -ForegroundColor Cyan
if (-not (Test-Path $build)) { throw "缺少 portable_build" }
New-Item -ItemType Directory -Force -Path $dist | Out-Null
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

$whitelist = @("backend", "frontend", "runtime", "docs", "scripts",
               "README.md", "start.bat", "启动.bat", "使用说明.txt")
foreach ($item in $whitelist) {
    $src = Join-Path $build $item
    if (Test-Path $src) { Copy-Item $src -Destination $stage -Recurse -Force }
}

# 清理运行时/临时/敏感内容
Remove-Item (Join-Path $stage "backend\data") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $stage "backend\vendor") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $stage "backend\models") -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem $stage -Recurse -File -Include "*.pyc", "*.db", "*.db-wal", "*.db-shm", "*.log" |
    Remove-Item -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $stage "backend\.env") -Force -ErrorAction SilentlyContinue

# 临时/探针文件扫描
$probes = Get-ChildItem $stage -Recurse -File | Where-Object { $_.Name -match "^_(battery|coldstart|diag|probe|cat_dbg|tmp|test_out)" -or $_.Name -like "*.log" }
if ($probes) { throw ("发现临时/探针文件：" + (($probes | Select-Object -First 5 -ExpandProperty FullName) -join ", ")) }

# 根目录白名单校验
$rootFiles = Get-ChildItem $stage -Force | Select-Object -ExpandProperty Name
$unexpected = $rootFiles | Where-Object { $whitelist -notcontains $_ }
if ($unexpected) { throw ("根目录存在白名单外文件：" + ($unexpected -join ", ")) }

if (Test-Path $zip) { Remove-Item $zip -Force }
[System.IO.Compression.ZipFile]::CreateFromDirectory($stage, $zip,
    [System.IO.Compression.CompressionLevel]::Optimal, $false)
Remove-Item $stage -Recurse -Force

$size = [math]::Round((Get-Item $zip).Length / 1MB, 2)
$count = ([System.IO.Compression.ZipFile]::OpenRead($zip)).Entries.Count
Write-Host ("已生成：{0}（{1} 文件 / {2} MB）" -f $zip, $count, $size) -ForegroundColor Green
