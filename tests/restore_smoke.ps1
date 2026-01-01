# scripts/ci_smoke.ps1
# Purpose:
#   - Run contract tests (pytest)
#   - Run prod gate check with P1_CONTRACT_VERIFIED=1
#   - Do not pollute caller's env (restore env after gate check)
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\ci_smoke.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "[ci_smoke] start"

# repo root (script may be executed from anywhere)
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

# venv
$venvActivate = Join-Path $repoRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
  Write-Host "[ci_smoke] activate venv"
  . $venvActivate
} else {
  Write-Error "[ci_smoke] venv not found: $venvActivate"
  exit 1
}

Write-Host "[ci_smoke] python=$(Get-Command python -ErrorAction Stop | Select-Object -ExpandProperty Source)"
python --version

# 1) Contract tests
Write-Host "[ci_smoke] running pytest"
python -m pytest
$exit1 = $LASTEXITCODE
Write-Host "[ci_smoke] pytest exit=$exit1"
if ($exit1 -ne 0) {
  Write-Error "[ci_smoke] FAIL (pytest)"
  exit $exit1
}

# 2) prod gate check (CI verified)
$tmpDir = Join-Path $repoRoot ".tmp"
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

$dbPath = Join-Path $tmpDir "ci-ledger.sqlite3"
Write-Host "[ci_smoke] prod gate check DB_PATH=$dbPath"

# Save env -> set env -> run -> restore env
$oldEnv = @{
  MODE                 = $env:MODE
  P1_CONTRACT_VERIFIED = $env:P1_CONTRACT_VERIFIED
  SESSION_SECRET       = $env:SESSION_SECRET
  ADMIN_PASSWORD       = $env:ADMIN_PASSWORD
  DEV_PASSWORD         = $env:DEV_PASSWORD
  DB_PATH              = $env:DB_PATH
}

try {
  $env:MODE = "prod"
  $env:P1_CONTRACT_VERIFIED = "1"
  $env:SESSION_SECRET = "test-secret"
  $env:ADMIN_PASSWORD = "admin"
  $env:DEV_PASSWORD = "dev"
  $env:DB_PATH = $dbPath

  python -c "import app; app.init_settings(); app.check_p1_contract_gate(); print('prod gate ok')"
}
finally {
  foreach ($k in $oldEnv.Keys) {
    if ($null -eq $oldEnv[$k]) {
      Remove-Item "Env:$k" -ErrorAction SilentlyContinue
    } else {
      Set-Item "Env:$k" $oldEnv[$k]
    }
  }
}

$exit2 = $LASTEXITCODE
Write-Host "[ci_smoke] gate exit=$exit2"
if ($exit2 -ne 0) {
  Write-Error "[ci_smoke] FAIL (prod gate)"
  exit $exit2
}

Write-Host "[ci_smoke] PASS"
exit 0
