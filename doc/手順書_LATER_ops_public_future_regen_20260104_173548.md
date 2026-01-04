# 手順書（LATER）: Ubuntu運用 + 公開の入口（TLS/レート制限） + 追加ハードニング + 未来強化

目的: **脳を汚さず、必要になったタイミングでだけ“入口/運用/追加品質”を足す。**  
前提: NOW（P1.5 /scan認可 + auth_gate + trusted proxy境界 + /scan契約テスト）が終わっていること。

---

## LATERの構成（読む順）
1. ローカル/LAN運用（Ubuntu）: まず「動かし続ける」ための最低限
2. 公開するなら「入口」最小構成: **FastAPI直晒し禁止** + TLS + レート制限
3. 追加ハードニング（アプリ側）: “やると強い”が今すぐ必須ではないもの
4. 未来強化（P2+）: 構造化・完了判定・監視など

---

# 1) Ubuntu運用（ローカル/LANで回す）

## 1-1. 前提パッケージ（初回）
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip sqlite3 git
```

（任意）
```bash
sudo apt install -y ripgrep ufw
```

## 1-2. 推奨ディレクトリ & セットアップ
```bash
sudo mkdir -p /opt/vnext-ledger
sudo chown -R "$USER":"$USER" /opt/vnext-ledger
cd /opt/vnext-ledger
git clone <YOUR_REPO_URL> .
```

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt
```

## 1-3. 起動（ローカル運用）
```bash
source .venv/bin/activate
export MODE=local
uvicorn app:app --host 127.0.0.1 --port 8000
```

- LAN内の別端末から見たい場合のみ `--host 0.0.0.0` にする  
  ※ ただし **ルータ越しの公開（ポート開放）はしない**（外聞ライン）

## 1-4. LANに絞る（任意: UFW）
SSH している場合は先に 22 を許可しないと締め出すことがある。

```bash
sudo ufw allow 22/tcp   # 必要な場合のみ
sudo ufw allow from <LAN_CIDR> to any port 8000 proto tcp
sudo ufw enable
sudo ufw status
```

## 1-5. 常駐（任意: systemd）
「常時起動するローカルサーバ」にする場合だけ。

`/etc/systemd/system/vnext-ledger.service`
```ini
[Unit]
Description=vNext Ledger (FastAPI)
After=network.target

[Service]
Type=simple
User=<YOUR_USER>
WorkingDirectory=/opt/vnext-ledger
Environment=MODE=local
ExecStart=/opt/vnext-ledger/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vnext-ledger
sudo systemctl status vnext-ledger --no-pager
journalctl -u vnext-ledger -n 200 --no-pager
```

## 1-6. SQLiteバックアップ（最小）
```bash
mkdir -p backups
cp -a data/ledger.db "backups/ledger_$(date +%Y%m%d_%H%M%S).db"
```

---

# 2) 公開するなら「入口」最小構成（TLS/レート制限/直晒し禁止）

> ここは **公開すると決めた時だけ**やる。ローカル/LANだけなら読まない。

## 2-1. Done Criteria（入口の最低ライン）
- FastAPI は **127.0.0.1** でのみ待受（外部到達不能）
- 外部公開は **HTTPS（TLS終端）** だけ
- `/auth/login` と `/scan` は **入口でレート制限**
- `blog.*` と `ledger.*` の **サブドメイン分離**（Cookie/CSP混線回避）

## 2-2. 入口の選択（おすすめ順）
- **Caddy**: 最短でHTTPS。まず成功させたい時向き
- **Nginx**: 王道。説明しやすい
- **Cloudflare**: 入口をクラウドに寄せたい時（ただし“原点”は必要）

## 2-3. アプリ側の待受（重要）
```bash
uvicorn app:app --host 127.0.0.1 --port 8000
```
アプリのポートはFW/SGで閉じる。外に出すのは 80/443 のみ。

## 2-4. 入口側の注意（auth系のキャッシュ禁止）
- `/auth/check` などを **キャッシュしない**（no-store）
- `Host / X-Forwarded-Proto / X-Forwarded-For` を転送する

> ※ NOWで入れた “trusted proxy 境界” がある前提。
> nginx/Caddyが同一ホストなら `TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128` を想定。
> 別ホスト/コンテナなら、実IP/CIDRに合わせて設定する。

---

# 3) 追加ハードニング（アプリ側）— 今すぐ必須ではないが“やると強い”

ここは「公開を見据える」「事故耐性を上げたい」タイミングで実施。

## 3-1. `/auth/logout` を CSRF で保護（ログアウトCSRF）
目的: 悪意あるサイトから「勝手にログアウト」させられるのを避けたい場合。

最小方針:
- `logout(request: Request)` にして、先頭で
  - `_verify_csrf_if_cookie_present(request)`
- ただし「UX優先（いつでも掃除できる）」なら、現状維持でも良い

## 3-2. slug 長さ制限（DoS耐性）
目的: 巨大slugで正規表現/DBが重くなるのを避ける。

最小:
- 抽出時に上限（例: 500）を入れる
- DBの CHECK 制約は **SQLite移行が重い**ので「未来」へ回す

## 3-3. `MODE=prod` の `SESSION_SECRET` 最小長チェック
目的: 弱いsecretでセッション改ざん耐性が落ちるのを防ぐ。

最小:
- prodで `len(SESSION_SECRET) < 32` を起動時に落とす

## 3-4. scan の encoding error 契約テスト
目的: バイナリ混入/非UTF8でscanが落ちないことをテストで固定。

最小テスト:
- tmpに非UTF8バイナリ + 正常NOTEを置く
- scanが200
- NOTEが拾えている

## 3-5. `/auth/login` レート制限（入口でやれない場合の保険）
公開前提なら **入口（Nginx/Caddy/CF）で落とすのが第一**。  
それでも保険としてアプリ内に簡易レート制限を足すのは有効。

---

# 4) 未来強化（P2+）— やると“仕様がテストになる”領域

## 4-1. init_settings と init_db の責務統一
- lifespanで `init_settings → gate → init_db` の順を崩さない
- 手順書/実装の矛盾が出ないように固定

## 4-2. “P1完了判定”をスクリプト化（機械で閉じる）
- `scripts/check_p1_complete.py` 等
- `pytest -q` + schema/version + 必須テーブル存在 などで PASS/FAIL

## 4-3. scan 構造化（diff/full分割）＋ Repo層（最小）
- `_run_diff_scan()` / `_run_full_scan()` に分割
- stale/orphan は full だけが触れる（呼び間違い不能）

## 4-4. セッション期限（exp）とローテ方針
- exp導入
- ローテを「全失効」か「世代管理」で設計

## 4-5. 監査ログ保護 / 監視 / DB制約の後付け計画
- 監査ログ改ざん耐性（追記専用/ハッシュチェーン等）
- 重要メトリクス（エラー率、scan時間、DBサイズ）
- slug CHECK 等の “後から入れづらい制約” の導入計画（SQLite移行含む）

---

## 参照（原典）
- 必須（外聞ライン/Ubuntu）: `実装手順書_必須_セキュリティ外聞ライン_Ubuntu_v6_NO_NGINX_regen_20260103_214912_c2cde135.md`
- 入口（TLS/レート制限）: `追記_入口最小構成_TLS_レート制限_20260103_215839_bd14394c.md`
- 未来（品質向上）: `実装手順書_未来_セキュリティ強化と品質向上_Ubuntu_v6_NO_NGINX_regen_20260103_214912_c2cde135.md`
