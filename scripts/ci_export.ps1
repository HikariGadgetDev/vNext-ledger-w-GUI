# scripts/ci_export.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Config (override via env)
$BASE = if ($env:BASE) { $env:BASE } else { "http://127.0.0.1:8000" }
$REPORT_DATE = if ($env:REPORT_DATE) { $env:REPORT_DATE } else { (Get-Date -Format "yyyy-MM-dd") }
$OUT = if ($env:OUT) { $env:OUT } else { (Join-Path "reports" $REPORT_DATE) }

New-Item -ItemType Directory -Force -Path $OUT | Out-Null

# PS 5.1 compatibility (avoids IE engine dependency)
$useBasicParsing = $true

function Invoke-GetToFile([string]$Url, [string]$OutFile) {
  Invoke-WebRequest `
    -Uri $Url `
    -Method GET `
    -TimeoutSec 60 `
    -OutFile $OutFile `
    -UseBasicParsing:$useBasicParsing | Out-Null
}

function Invoke-PostJsonToFile([string]$Url, [string]$JsonBody, [string]$OutFile) {
  Invoke-WebRequest `
    -Uri $Url `
    -Method POST `
    -ContentType "application/json" `
    -Body $JsonBody `
    -TimeoutSec 60 `
    -OutFile $OutFile `
    -UseBasicParsing:$useBasicParsing | Out-Null
}

Write-Host "[ci_export] BASE=$BASE"
Write-Host "[ci_export] REPORT_DATE=$REPORT_DATE"
Write-Host "[ci_export] OUT=$OUT"

# diff scan (full=0)
Invoke-PostJsonToFile "$BASE/scan?full=0" "{}" (Join-Path $OUT "scan.json")

# exports
Invoke-GetToFile "$BASE/export/summary" (Join-Path $OUT "summary.json")
Invoke-GetToFile "$BASE/export/notes"   (Join-Path $OUT "notes.json")
Invoke-GetToFile "$BASE/export/metrics?limit=50" (Join-Path $OUT "metrics.json")

# (optional) JSON sanity check (only if python exists)
$py = Get-Command python -ErrorAction SilentlyContinue
if ($null -ne $py) {
  python -m json.tool (Join-Path $OUT "summary.json") | Out-Null
  python -m json.tool (Join-Path $OUT "notes.json")   | Out-Null
  python -m json.tool (Join-Path $OUT "metrics.json") | Out-Null
} else {
  Write-Host "[ci_export] python not found; skip json sanity check" -ForegroundColor Yellow
}

Write-Host "wrote $OUT"
exit 0
