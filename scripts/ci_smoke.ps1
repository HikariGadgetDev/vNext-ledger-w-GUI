# scripts/ci_smoke.ps1

# Purpose: CI smoke test (automated, stateless)
# 目的: CIスモークテスト（自動・ステートレス）

# - Always cleanup temp files (success or failure) to keep the workspace clean
# - 一時ファイルは成功/失敗に関わらず必ず削除し、ワークスペースを常にクリーンに保つ
# - Do not rely on leftover artifacts for debugging
# - デバッグ目的で成果物（残骸）が残ることを前提にしない
# - For debugging a failing gate, run restore_smoke.ps1 (keeps artifacts) or reproduce locally
# - ゲート失敗のデバッグは restore_smoke.ps1（成果物を残す）を使うか、ローカルで再現して行う

# Rationale: CI must be repeatable; leftover artifacts can mask failures and accumulate over time.
# 理由: CIは再現性が必須であり、残骸があると失敗を覆い隠したり、時間とともに蓄積して問題を起こす

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "[ci_smoke] start"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

# venv
$venvActivate = Join-Path $repoRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
  . $venvActivate
} else {
  Write-Error "[ci_smoke] venv not found: $venvActivate"
}

Write-Host "[ci_smoke] python=$(Get-Command python -ErrorAction Stop | Select-Object -ExpandProperty Source)"
python --version

# 1) Contract tests
Write-Host "[ci_smoke] running pytest"
python -m pytest -q
$exit1 = $LASTEXITCODE
Write-Host "[ci_smoke] pytest exit=$exit1"
if ($exit1 -ne 0) { Write-Error "[ci_smoke] FAIL (pytest)"; exit $exit1 }

# 2) prod gate check (CI verified)
$tmpDir = Join-Path $repoRoot ".tmp"
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
$dbPath = Join-Path $tmpDir "ci-ledger.sqlite3"
Write-Host "[ci_smoke] prod gate check DB_PATH=$dbPath"

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
$env:SESSION_SECRET = "dev-secret-for-ci"
$env:ADMIN_PASSWORD = "admin"
$env:DEV_PASSWORD = "dev"
$env:DB_PATH = $dbPath

# run gate with guaranteed cleanup
$exit2 = 0
try {
  # gatePy in repoRoot to ensure 'import app' works
  $gatePy = Join-Path $repoRoot ".ci_gate_temp.py"
  $gateCode = @(
    "import app",
    "app.init_settings()",
    "app.check_p1_contract_gate()",
    'print("prod gate ok")'
  ) -join "`n"

  Set-Content -Path $gatePy -Value $gateCode -Encoding UTF8

  python $gatePy
  $exit2 = $LASTEXITCODE
  Write-Host "[ci_smoke] gate exit=$exit2"
} finally {
  # cleanup temp files (including SQLite WAL files)
  Remove-Item $gatePy -ErrorAction SilentlyContinue
  Remove-Item $dbPath -ErrorAction SilentlyContinue
  Remove-Item "$dbPath-wal" -ErrorAction SilentlyContinue
  Remove-Item "$dbPath-shm" -ErrorAction SilentlyContinue
  Remove-Item "$dbPath-journal" -ErrorAction SilentlyContinue
  
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
}

if ($exit2 -ne 0) { Write-Error "[ci_smoke] FAIL (prod gate)"; exit $exit2 }

Write-Host "[ci_smoke] PASS"
exit 0