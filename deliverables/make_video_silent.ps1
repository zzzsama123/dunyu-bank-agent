$ErrorActionPreference = "Stop"
$base = $PSScriptRoot
$pptx = (Resolve-Path (Join-Path $base "盾语_答辩PPT.pptx")).Path
$mp4 = Join-Path $base "盾语_演示视频.mp4"
$ppt = New-Object -ComObject PowerPoint.Application
$ppt.Visible = -1
$ppt.DisplayAlerts = 1
$pres = $ppt.Presentations.Open($pptx, -1, 0, -1)
try {
    $pres.SlideShowSettings.ShowWithAnimation = -1
    if (Test-Path $mp4) { Remove-Item $mp4 -Force }
    $pres.CreateVideo($mp4, 0, 18, 1080, 30, 85)
    $deadline = (Get-Date).AddMinutes(5)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        if (Test-Path $mp4) {
            $len = (Get-Item $mp4).Length
            if ($len -gt 100000) {
                Start-Sleep -Seconds 2
                if ((Get-Item $mp4).Length -eq $len) { break }
            }
        }
    }
    Write-Host ("VIDEO " + (Get-Item $mp4).Length + " bytes")
} finally {
    $pres.Close()
    $ppt.Quit()
}