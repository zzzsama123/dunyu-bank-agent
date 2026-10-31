# 给答辩 PPT 嵌入旁白并导出 MP4（Windows PowerPoint COM）
$ErrorActionPreference = "Stop"
$base = $PSScriptRoot
$pptx = (Resolve-Path (Join-Path $base "盾语_答辩PPT.pptx")).Path
$outPptx = Join-Path $base "盾语_答辩PPT_带旁白.pptx"
$mp4 = Join-Path $base "盾语_演示视频.mp4"
$durations = Get-Content (Join-Path $base "audio\durations.json") -Raw -Encoding UTF8 | ConvertFrom-Json

$ppt = New-Object -ComObject PowerPoint.Application
$ppt.Visible = -1
$ppt.DisplayAlerts = 1
$pres = $ppt.Presentations.Open($pptx, -1, 0, -1)
try {
    for ($i = 1; $i -le $pres.Slides.Count; $i++) {
        $slide = $pres.Slides.Item($i)
        $wav = Join-Path $base ("audio\slide{0}.wav" -f $i)
        $shape = $slide.Shapes.AddMediaObject2($wav, 0, -1, 8, 8, 24, 24)
        $shape.Name = "Narration"
        $shape.AnimationSettings.PlaySettings.PlayInShow = -1
        $shape.AnimationSettings.PlaySettings.HideWhileNotPlaying = -1
        $shape.AnimationSettings.PlaySettings.LoopUntilStopped = 0
        $dur = [double]$durations.$i + 0.8
        $slide.SlideShowTransition.AdvanceOnTime = -1
        $slide.SlideShowTransition.AdvanceTime = $dur
    }
    $pres.SaveAs($outPptx)
    $pres.SlideShowSettings.ShowWithNarration = -1
    $pres.SlideShowSettings.ShowWithAnimation = -1
    if (Test-Path $mp4) { Remove-Item $mp4 -Force }
    $pres.CreateVideo($mp4, -1, 5, 1080, 30, 85)
    $deadline = (Get-Date).AddMinutes(6)
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
