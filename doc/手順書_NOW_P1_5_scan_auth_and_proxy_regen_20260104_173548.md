# 手順書（NOW）— P1.5「/scan を締める」最小セット（この通り打てば終わる版）

対象: **いまやる範囲だけ**（/scan 認可 + 認証ゲート集約 + Reverse Proxy信頼境界 + 契約テスト + 最小スモーク）  
前提: 既存の vNext-ledger リポジトリが動いている（pytest が通っている状態から入る想定）

---

## ゴール（Done Criteria）
- ✅ `MODE=prod` で **未ログインの POST /scan が 401/403**（cookie無し直叩き不可）
- ✅ `MODE=local` で **localhost + JSON + ALLOW_LOCAL_JSON_NOAUTH=1 の時だけ /scan が 200**
- ✅ `pytest` が **全緑**（既存 + 追加テスト）
- ✅ reverse proxy 対応: `X-Forwarded-*` は **trusted proxy の時だけ**信頼（直アクセス偽装が効かない）
- ✅ local 例外が **proxy越しに誤発動しない**（XFF 左端が remote なら無効）

---

## 0) 作業ブランチ（必須）
### bash
```bash
git checkout -b p1_5_scan_auth_and_proxy
```

### PowerShell
```powershell
git checkout -b p1_5_scan_auth_and_proxy
```

---

## 1) 適用方法の選択（A推奨）
### A) diff を当てる（推奨・安全）
リポジトリ直下に `p1_5_patch_app_py.diff` を置いてから:

```bash
git apply p1_5_patch_app_py.diff
```

PowerShell も同じ:
```powershell
git apply p1_5_patch_app_py.diff
```

### B) app.py を置き換える（最短だが雑）
`app_p1_5_patched.py` の内容で `app.py` を上書きする。

- 例（bash）:
```bash
cp app_p1_5_patched.py app.py
```

- 例（PowerShell）:
```powershell
Copy-Item app_p1_5_patched.py app.py -Force
```

---

## 2) 追加テストを配置（必須）
`tests/` が無いなら作る。

### bash
```bash
mkdir -p tests
cp test_p1_5_scan_auth_contract.py tests/test_p1_5_scan_auth_contract.py
```

### PowerShell
```powershell
New-Item -ItemType Directory -Force tests | Out-Null
Copy-Item test_p1_5_scan_auth_contract.py tests/test_p1_5_scan_auth_contract.py -Force
```

---

## 3) まずは差分確認（推奨）
```bash
git status
git diff --stat
```

---

## 4) pytest（ここで閉じる）
### bash
```bash
pytest -q
```

### PowerShell
```powershell
python -m pytest -q
```

✅ ここが全緑なら「コードの整合」はほぼ終了。  
❌ 落ちたら、そのログ（失敗の最初～最後）を貼れば、私が“いつもの磨き”で詰める。

---

## 5) 最小スモーク（local / prod）
### 5-1) local（localhost+JSON の例外確認）
#### bash
```bash
export MODE=local
export ALLOW_LOCAL_JSON_NOAUTH=1
export SESSION_SECRET='xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
uvicorn app:app --host 127.0.0.1 --port 8000
```

#### PowerShell
```powershell
$env:MODE="local"
$env:ALLOW_LOCAL_JSON_NOAUTH="1"
$env:SESSION_SECRET=("x"*64)
uvicorn app:app --host 127.0.0.1 --port 8000
```

別ターミナルで:
```bash
curl -i -H 'Accept: application/json' -X POST http://127.0.0.1:8000/scan -d '{"root":"."}'
```

期待:
- **200**（local JSON 例外が成立）

---

### 5-2) prod（未ログインで /scan が落ちる）
#### bash
```bash
export MODE=prod
export P1_CONTRACT_VERIFIED=1
export ADMIN_PASSWORD='x'
export SESSION_SECRET='xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
uvicorn app:app --host 127.0.0.1 --port 8000
```

#### PowerShell
```powershell
$env:MODE="prod"
$env:P1_CONTRACT_VERIFIED="1"
$env:ADMIN_PASSWORD="x"
$env:SESSION_SECRET=("x"*64)
uvicorn app:app --host 127.0.0.1 --port 8000
```

別ターミナルで:
```bash
curl -i -H 'Accept: application/json' -X POST http://127.0.0.1:8000/scan -d '{"root":"."}'
```

期待:
- **401/403**（cookie無し直叩き不可）

---

## 6) reverse proxy “信頼境界” の最小確認（今の段階でやる価値がある1個）
### 直アクセスで X-Forwarded-Proto を盛っても挙動が変わらない
（※ trusted proxy でない限り、Forwarded を信頼しないため）

```bash
curl -i -H 'X-Forwarded-Proto: https' http://127.0.0.1:8000/auth/check
```

期待:
- HTTPS扱いに “勝手に” 変わらない（secure cookie 化などの副作用が出ない）

---

## 7) nginx を噛ませる未来に備える（いまは env だけ覚えておけばOK）
同一ホストで nginx→uvicorn を想定するなら **これだけ**：

```bash
export TRUSTED_PROXY_CIDRS='127.0.0.1/32,::1/128'
```

（別ホスト / コンテナ構成になったら、その時に CIDR を見直す）

---

## 8) コミット（任意だが強く推奨）
```bash
git add -A
git commit -m "P1.5: protect /scan + auth gate + trusted proxy boundary + contract tests"
```

---

## 9) 失敗時の戻し（脳を守る）
### まだコミットしてない
```bash
git reset --hard
git clean -fd
```

### コミット済み（ブランチごと捨てる）
```bash
git checkout main
git branch -D p1_5_scan_auth_and_proxy
```

---

## 参照（このNOW手順書の素材）
- `p1_5_patch_app_py.diff`
- `app_p1_5_patched.py`
- `test_p1_5_scan_auth_contract.py`
