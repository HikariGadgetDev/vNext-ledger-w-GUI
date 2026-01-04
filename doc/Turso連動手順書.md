# vNext Ledger リファクタ手順書
（Vercel + Turso / オフライン両立版）

## 目的
- ローカル：SQLite 自動生成で即動く
- 本番（Vercel）：Turso（libSQL）で安全に動く
- 切り替えは環境変数のみ
- 既存ロジック・SQL・UI を壊さない

## 全体方針
- DB切替は設定で決める
- SQLは書き換えない
- Repo/DB層を1点差し替え
- P0–P2完了後に着手

## フェーズ構成
### P3-0 前提確認
- MODE分離
- 認証/CSRF/CSP集約
- scan責務固定
- init_db()冪等

### P3-1 DB設定
```
DB_BACKEND=sqlite|turso
SQLITE_PATH=ledger.sqlite3
TURSO_DATABASE_URL=...
TURSO_AUTH_TOKEN=...
```

### P3-2 DB層分離
vnext_ledger/db 以下に base/sqlite/turso

### P3-3 SQLite実装
- 自動生成
- 挙動一致

### P3-4 Turso実装
- libsql
- IF NOT EXISTS

### P3-5 db()統合
envで切替

### P3-6 Vercel薄皮
api/index.py で app import

### P3-7 環境変数
MODE=prod 他

### P3-8 動作確認
- local OK
- vercel OK

## 完成条件
- DB切替可能
- SQL1系統
- UI壊れない
