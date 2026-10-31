param(
  [Parameter(Mandatory = $true)][string]$Wav,
  [string]$PhrasesFile = "",
  [Parameter(Mandatory = $true)][string]$OutFile
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech

$rec = New-Object System.Speech.Recognition.SpeechRecognitionEngine
$rec.InitialSilenceTimeout = [TimeSpan]::FromSeconds(2)
$rec.BabbleTimeout = [TimeSpan]::FromSeconds(2)
$rec.EndSilenceTimeout = [TimeSpan]::FromSeconds(0.6)

# 自由听写
$rec.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))

# 受限命令语法：把常用说法和"给某人转多少钱"直接列进去，识别更稳
if ($PhrasesFile -and (Test-Path -LiteralPath $PhrasesFile)) {
  $phrases = Get-Content -LiteralPath $PhrasesFile -Encoding UTF8 | Where-Object { $_.Trim() }
  if ($phrases.Count -gt 0) {
    $choices = New-Object System.Speech.Recognition.Choices
    foreach ($p in $phrases) { $choices.Add($p.Trim()) }
    $builder = New-Object System.Speech.Recognition.GrammarBuilder($choices)
    $grammar = New-Object System.Speech.Recognition.Grammar($builder)
    $rec.LoadGrammar($grammar)
  }
}

$rec.SetInputToWaveFile($Wav)
$result = $rec.Recognize()
$payload = @{
  text = if ($result) { $result.Text } else { "" }
  confidence = if ($result) { [math]::Round($result.Confidence, 4) } else { 0 }
  engine = "sapi-zh-CN"
}
$alts = @()
if ($result) {
  foreach ($alt in $result.Alternatives) {
    $alts += @{ text = $alt.Text; confidence = [math]::Round($alt.Confidence, 4) }
  }
}
$payload["alternatives"] = $alts
$json = $payload | ConvertTo-Json -Compress -Depth 5
[System.IO.File]::WriteAllText($OutFile, $json, (New-Object System.Text.UTF8Encoding($false)))
$rec.Dispose()
