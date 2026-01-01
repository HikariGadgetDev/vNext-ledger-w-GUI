# scripts/restore_smoke.ps1

# Purpose: Smoke test with artifact preservation for debugging
# 目的: デバッグ用に成果物を残すスモークテスト

# - Keep temp files (DB, .py) for post-failure inspection
# - 一時ファイル（DB、.py）を失敗後の調査のために残す
# - Useful for debugging gate failures locally
# - ゲート失敗をローカルでデバッグする際に有用
# - For CI (automated, stateless), use ci_smoke.ps1 instead
# - CI（自動・ステートレス）では ci_smoke.ps1 を使用すること

# Rationale: Manual debugging often needs leftover artifacts to inspect state.
# 理由: 手動デバッグでは、状態を調査するために残骸が必要なことが多い。

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "[restore_smoke] start"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

# venv
$venvActivate = Join-Path $repoRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
  . $venvActivate
} else {
  Write-Error "[restore_smoke] venv not found: $venvActivate"
}

Write-Host "[restore_smoke] python=$(Get-Command python -ErrorAction Stop | Select-Object -ExpandProperty Source)"
python --version

# 1) Contract tests
Write-Host "[restore_smoke] running pytest"
python -m pytest -q
$exit1 = $LASTEXITCODE
Write-Host "[restore_smoke] pytest exit=$exit1"
if ($exit1 -ne 0) { Write-Error "[restore_smoke] FAIL (pytest)"; exit $exit1 }

# 2) prod gate check (CI verified)
$tmpDir = Join-Path $repoRoot ".tmp"
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

# Use timestamp to avoid overwriting previous debugging sessions
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$dbPath = Join-Path $tmpDir "restore-ledger-$stamp.sqlite3"
# gatePy in repoRoot (with timestamp) to ensure 'import app' works
$gatePy = Join-Path $repoRoot ".restore_gate_temp_$stamp.py"

Write-Host "[restore_smoke] prod gate check DB_PATH=$dbPath"

# save old env (存在チェック版: 空文字 vs 未定義を区別)
$envKeys = @("MODE", "P1_CONTRACT_VERIFIED", "SESSION_SECRET", "ADMIN_PASSWORD", "DEV_PASSWORD", "DB_PATH")
$oldEnv = @{}
foreach ($k in $envKeys) {
  $exists = Test-Path "Env:\$k"
  $val = $null
  if ($exists) { $val = (Get-Item "Env:\$k").Value }
  $oldEnv[$k] = @{ exists = $exists; value = $val }
}

# set env for gate
$env:MODE = "prod"
$env:P1_CONTRACT_VERIFIED = "1"
$env:SESSION_SECRET = "dev-secret-for-restore"
$env:ADMIN_PASSWORD = "admin"
$env:DEV_PASSWORD = "dev"
$env:DB_PATH = $dbPath

# run gate with artifact preservation
$exit2 = 0
try {
  $gateCode = @(
    "import app",
    "app.init_settings()",
    "app.check_p1_contract_gate()",
    'print("prod gate ok")'
  ) -join "`n"

  Set-Content -Path $gatePy -Value $gateCode -Encoding UTF8

  python $gatePy
  $exit2 = $LASTEXITCODE
  Write-Host "[restore_smoke] gate exit=$exit2"
} finally {
  # Always show artifacts location (even if exception occurred)
  Write-Host "[restore_smoke] artifacts dir=$tmpDir" -ForegroundColor Cyan
  
  # restore env (存在ベース復元: 空文字と未定義を正しく区別)
  # try-finally により中断時も確実に復元
  foreach ($k in $envKeys) {
    if ($oldEnv[$k].exists) {
      if ($null -eq $oldEnv[$k].value) {
        Remove-Item "Env:$k" -ErrorAction SilentlyContinue
      } else {
        Set-Item "Env:$k" $oldEnv[$k].value
      }
    } else {
      Remove-Item "Env:$k" -ErrorAction SilentlyContinue
    }
  }
  
  # NOTE: Artifacts are intentionally NOT cleaned up (success or failure)
  # - This is a debugging-oriented script; use ci_smoke.ps1 for CI (stateless)
  # - $gatePy (.restore_gate_temp_TIMESTAMP.py) is kept in repoRoot for inspection
  # - $dbPath (restore-ledger-TIMESTAMP.sqlite3 + -wal/-shm) is kept in .tmp
  # To clean up manually: Remove-Item .restore_gate_temp_*.py, .tmp\restore-*
}

if ($exit2 -ne 0) {
  Write-Host "[restore_smoke] FAIL (prod gate) - artifacts preserved for debugging:" -ForegroundColor Yellow
  Write-Host "  Directory: $tmpDir" -ForegroundColor Cyan
  Write-Host "  DB:        $dbPath" -ForegroundColor Yellow
  Write-Host "  WAL:       ${dbPath}-wal" -ForegroundColor Yellow
  Write-Host "  SHM:       ${dbPath}-shm" -ForegroundColor Yellow
  Write-Host "  Script:    $gatePy" -ForegroundColor Yellow
  Write-Host ""
  Write-Host "Inspect these files to debug the failure" -ForegroundColor Yellow
  exit $exit2
}

Write-Host "[restore_smoke] PASS - artifacts preserved at:"
Write-Host "  Directory: $tmpDir" -ForegroundColor Cyan
Write-Host "  DB:        $dbPath"
Write-Host "  WAL:       ${dbPath}-wal"
Write-Host "  SHM:       ${dbPath}-shm"
Write-Host "  Script:    $gatePy"
Write-Host ""
Write-Host "NOTE: Artifacts are kept for debugging (even on success)" -ForegroundColor Yellow
Write-Host "To clean up: Remove-Item .restore_gate_temp_*.py, .tmp\restore-*" -ForegroundColor Gray
exit 0