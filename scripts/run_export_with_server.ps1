# scripts/run_export_with_server.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$BASE = if ($env:BASE) { $env:BASE } else { "http://127.0.0.1:8000" }
$uri = [Uri]$BASE
$bindHost = $uri.Host
$bindPort = $uri.Port

Write-Host "[run_export_with_server] start BASE=$BASE (host=$bindHost port=$bindPort)"

# まず「起動済みか？」を確認
$startedHere = $false
$uvicorn = $null

function Test-ServerReady([string]$base) {
  try {
    Invoke-WebRequest -Uri "$base/export/summary" -TimeoutSec 1 -UseBasicParsing | Out-Null
    return $true
  } catch {
    return $false
  }
}

if (-not (Test-ServerReady $BASE)) {
  Write-Host "[run_export_with_server] server not running; starting uvicorn..."

  # venv を使いたいならここで activate（ci_smoke と揃える）
  $venvActivate = Join-Path $repoRoot ".venv\Scripts\Activate.ps1"
  if (Test-Path $venvActivate) { . $venvActivate }

  $uvicorn = Start-Process `
    -FilePath "python" `
    -ArgumentList @("-m","uvicorn","app:app","--host",$bindHost,"--port",$bindPort) `
    -WorkingDirectory $repoRoot `
    -PassThru `
    -WindowStyle Hidden

  $startedHere = $true
  Write-Host "[run_export_with_server] startedHere=true pid=$($uvicorn.Id)" -ForegroundColor Cyan

  # ready wait (max ~10s)
  $ready = $false
  for ($i=0; $i -lt 20; $i++) {
    if (Test-ServerReady $BASE) { $ready = $true; break }
    Start-Sleep -Milliseconds 500
  }
  if (-not $ready) {
    throw "[run_export_with_server] server not ready: $BASE"
  }
} else {
  Write-Host "[run_export_with_server] server already running; reuse it"
  Write-Host "[run_export_with_server] startedHere=false" -ForegroundColor Cyan
}

# Save original BASE env (呼び出し元を汚さない)
$oldBase = $null
$hadBase = Test-Path Env:\BASE
if ($hadBase) { $oldBase = (Get-Item Env:\BASE).Value }

try {
  # Pass BASE to child process to ensure URL consistency
  $env:BASE = $BASE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ci_export.ps1
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) { exit $exitCode }
}
finally {
  # Restore BASE env (呼び出し元を汚さない)
  if ($hadBase) { 
    Set-Item Env:\BASE $oldBase 
  } else { 
    Remove-Item Env:\BASE -ErrorAction SilentlyContinue 
  }
  
  # Stop uvicorn if we started it (残党狩り付き)
  if ($startedHere -and $uvicorn -and -not $uvicorn.HasExited) {
    Write-Host "[run_export_with_server] stopping uvicorn pid=$($uvicorn.Id)"
    Stop-Process -Id $uvicorn.Id -Force -ErrorAction SilentlyContinue
    
    # Verify port is actually closed (残党狩り)
    for ($i=0; $i -lt 10; $i++) {
      if (-not (Test-ServerReady $BASE)) { break }
      Start-Sleep -Milliseconds 200
    }
  }
}

Write-Host "[run_export_with_server] done"
exit 0