# vNext Ledger P1 実装手順書（完全版 - L7精査済み）

## 📋 目的

**「契約をテストで固定し、将来壊れない構造を作る」**

- 既存テストの契約を崩さない
- diff/full scan の規約を関数境界で保証
- 既存DBとの後方互換性確保
- 台帳汚染防止（no-op契約）

---

## 🎯 P1 の範囲と優先度

| 項目 | 優先度 | 工数 | 理由 |
|------|--------|------|------|
| **P1-1: 既存DB後方互換** | 🔴 最重要 | 1h | 古いDBで落ちる |
| **P1-2: /export/notes フィルタ** | 🔴 最重要 | 30m | 契約テスト必須 |
| **P1-3: PATCH契約修正** | 🔴 最重要 | 1h | 既存テスト必須 |
| **P1-4: init_settings→init_db連携** | 🔴 最重要 | 15m | テスト実行時必須 |
| **P1.5-A: slug長さ制限** | 🟡 重要 | 30m | DoS防止 |
| **P1.5-B: SESSION_SECRET長** | 🟡 重要 | 15m | セキュリティ |
| **P1.5-C: encoding error** | 🟡 重要 | 15m | クラッシュ防止 |
| **P1-構造化（diff/full分割）** | 🟢 次点 | 3-4h | 構造固定 |
| **P1-Repo層** | 🟢 次点 | 2-3h | 保守性 |

**合計工数**: 8-11時間（1.5-2日）

---

## 既存テスト契約（前提）

### 採用するテスト（P1 で緑にする）
- ✅ `tests/test_export_and_scan.py`
- ✅ `tests/test_patch_notes.py`

### 採用しないテスト（P1.5 以降）
- ❌ `tests/test_p1_ledger_pollution_contract.py`（fixture不足＆契約衝突）
  - 理由: no-op判定でupdated_atを変えない等はP1.5で実装
  - 対応: pytest対象外に退避（ファイル移動OK、中身改変禁止）

---

## 禁止事項（去勢リスト）

### ❌ 絶対禁止
- 関数分割・共通化・命名変更（P1-構造化を除く）
- 型整理・SQL整形・コメント整備
- 既存の `CREATE TABLE` 文の"改善"
- `tests/` の修正（退避除く）
- アーキテクチャ変更

### ✅ 許可
- P1-1: `_ensure_column()` helper 追加
- P1-2: `export_notes()` 引数・SQL追加
- P1-3: `PATCH /notes/{slug}` ロジック修正
- P1-4: `init_settings()` 末尾に `init_db()` 追加
- P1.5: 小さな安全性改善（3行以内）
- P1-構造化: scan関数分割（明示的に許可）
- P1-Repo層: Repo クラス導入（明示的に許可）

---

## P1-1: 既存DB後方互換（カラム追加）

### 🔴 優先度: 最重要（CRITICAL）
### ⏱ 工数: 1時間

### 問題

**現状**:
- 古いDBには `file_state.mtime_ns` 等がない → `/scan` が落ちる
- テストが `notes` に直接 INSERT → `is_deleted` 等がない → 落ちる

**影響**:
- 既存環境でアプリが起動しない
- テストが実行できない

---

### 対策

#### 1. `_ensure_column()` helper の追加

**ファイル**: `app.py`  
**場所**: `db()` の直後、`init_db()` の直前（Database セクション内）

```python
def _ensure_column(con: sqlite3.Connection, table: str, col: str, ddl: str) -> None:
    """
    テーブルにカラムが存在しない場合、追加する。
    既存DBとの後方互換性を保つため。
    """
    cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    if col not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
```

**注意**: これ以外のhelperは作らない（最小変更原則）

---

#### 2. `init_db()` 内でカラム追加

**ファイル**: `app.py`  
**場所**: `init_db()` の `CREATE TABLE` 群が終わった後、`with db() as con:` の中

```python
def _init_db():
    with db() as con:
        # ... 既存の CREATE TABLE ...
        
        # --- P1 backward-compat columns (do not refactor) ---
        # notes: required by tests/export contract
        _ensure_column(con, "notes", "first_seen", "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z'")
        _ensure_column(con, "notes", "last_seen", "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z'")
        _ensure_column(con, "notes", "evidence_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(con, "notes", "is_deleted", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(con, "notes", "is_archived", "INTEGER NOT NULL DEFAULT 0")

        # file_state: older DB safety (harmless if already present)
        _ensure_column(con, "file_state", "mtime_ns", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(con, "file_state", "size_bytes", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(con, "file_state", "last_seen_at", "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z'")
        
        con.commit()
```

**重要**:
- `ALTER TABLE ... ADD COLUMN ... NOT NULL` は**必ずDEFAULTを付ける**（SQLite仕様）
- `CREATE TABLE notes(...)` を書き換えて"綺麗にする"のは禁止（差分最小）

---

### 受け入れ条件

- [ ] 古いDBで `/scan` が落ちない
- [ ] テストが `notes` に直接 INSERT しても落ちない
- [ ] `PRAGMA table_info(notes)` で全カラムが存在する
- [ ] `PRAGMA table_info(file_state)` で全カラムが存在する

---

## P1-2: /export/notes フィルタ

### 🔴 優先度: 最重要（CRITICAL）
### ⏱ 工数: 30分

### 問題

**現状**:
- `GET /export/notes` が `is_deleted=1` や `is_archived=1` も返す

**契約テスト**:
- `test_export_notes_excludes_deleted_and_archived_by_default`
- `test_export_notes_can_include_deleted_and_archived`

---

### 対策

#### 1. 引数の追加

**ファイル**: `app.py`  
**場所**: `@app.get("/export/notes")` の関数定義

```python
# Before
@app.get("/export/notes")
def export_notes(request: Request):

# After
@app.get("/export/notes")
def export_notes(request: Request, include_deleted: int = 0, include_archived: int = 0):
```

**注意**:
- 型は `int` 固定（0/1運用）
- `Query()` は不要（変更を増やさない）

---

#### 2. WHERE 条件の追加

**ファイル**: `app.py`  
**場所**: `export_notes()` 内、`with db() as con:` の直前

```python
def export_notes(request: Request, include_deleted: int = 0, include_archived: int = 0):
    # WHERE 条件構築
    where = []
    if not include_deleted:
        where.append("n.is_deleted = 0")
    if not include_archived:
        where.append("n.is_archived = 0")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    
    with db() as con:
        rows = con.execute(
            f"""
            SELECT n.id, n.slug, n.status, n.priority, n.created_at, n.updated_at,
                   COUNT(e.id) as evidence_count
            FROM notes n
            LEFT JOIN evidence e ON n.id = e.note_id
            {where_sql}
            GROUP BY n.id
            ORDER BY n.updated_at DESC
            """
        ).fetchall()
```

**重要**:
- `FROM notes n` の alias `n` を使う
- `{where_sql}` を **LEFT JOINの後、GROUP BYの前**に差し込む
- COALESCEなどで誤魔化さない（列はDB側で保証されている前提）

---

### 受け入れ条件

- [ ] `GET /export/notes` は `is_deleted=0` かつ `is_archived=0` のみ返す
- [ ] `GET /export/notes?include_deleted=1` は削除済みも返す
- [ ] `GET /export/notes?include_archived=1` はアーカイブ済みも返す
- [ ] `GET /export/notes?include_deleted=1&include_archived=1` は全て返す

---

## P1-3: PATCH /notes/{slug} 契約修正

### 🔴 優先度: 最重要（CRITICAL）
### ⏱ 工数: 1時間

### 問題

**契約テスト**: `tests/test_patch_notes.py`

**要件**:
1. 空JSON `{}` → 204（DB更新しない）
2. 返却形式は `{"note": {...}}`
3. 末尾スラッシュ対応

---

### 対策

#### 1. 空JSON判定

**ファイル**: `app.py`  
**場所**: `@app.patch("/notes/{slug}")` 関数の先頭

```python
@app.patch("/notes/{slug}")
def update_note(slug: str, req: UpdateNoteRequest, request: Request):
    # CSRF/認証チェック（既存）
    _verify_csrf_if_cookie_present(request)
    # ...
    
    # 空JSON → 204
    fields_set = getattr(req, "model_fields_set", set())
    if not fields_set:
        return Response(status_code=204)
    
    # ... 既存のロジック ...
```

**注意**: P1.6の実装（no-op判定）はここでは不要（P1.5で追加）

---

#### 2. 返却形式の修正

**ファイル**: `app.py`  
**場所**: `@app.patch("/notes/{slug}")` 関数の末尾

```python
# Before
return {"status": new_status, "priority": new_priority}

# After
with db() as con:
    # ... UPDATE処理 ...
    
    updated_row = con.execute("SELECT * FROM notes WHERE slug = ?", (slug,)).fetchone()
    con.commit()

return {"note": dict(updated_row)}
```

---

#### 3. 末尾スラッシュ対応

**ファイル**: `app.py`  
**場所**: `@app.patch()` デコレータ

```python
# Before
@app.patch("/notes/{slug}")

# After（trailing slash許可）
@app.patch("/notes/{slug}")
@app.patch("/notes/{slug}/")  # ← 追加
def update_note(slug: str, req: UpdateNoteRequest, request: Request):
    # ... 既存のロジック ...
```

**注意**: FastAPIのデフォルト挙動で既にOKなら触らない

---

### 受け入れ条件

- [ ] `PATCH /notes/{slug}` に `{}` → 204
- [ ] `PATCH /notes/{slug}` に更新 → 200 + `{"note": {...}}`
- [ ] `PATCH /notes/test/` が動く（trailing slash）
- [ ] `tests/test_patch_notes.py` が全て緑

---

## P1-4: init_settings() → init_db() 連携

### 🔴 優先度: 最重要（CRITICAL）
### ⏱ 工数: 15分

### 問題

**現状**:
- テストの `conftest.py` は `init_settings()` しか呼ばない
- `init_db()` が呼ばれない → カラム追加が実行されない → テスト落ちる

---

### 対策

**ファイル**: `app.py`  
**場所**: `init_settings()` の末尾

```python
def init_settings():
    global _settings
    # ... 既存の設定読み込み ...
    
    _settings = settings
    
    # P1: Ensure DB initialization (for tests)
    _init_db()  # ← 追加
```

**注意**:
- startupイベント追加などは不要（余計な変更禁止）
- これで `file_state` の `_ensure_column` が実行される

---

### 受け入れ条件

- [ ] `init_settings()` を呼ぶと `init_db()` も呼ばれる
- [ ] テスト実行時にカラムが自動追加される
- [ ] `/scan` がテストで落ちない

---

## P1.5-A: slug 長さ制限（DoS防止）

### 🟡 優先度: 重要（HIGH）
### ⏱ 工数: 30分

### 問題

**現状**:
- slug に長さ制限がない → 極端に長い slug でDoS可能
- URL長さ制限（2000文字）を超える可能性

---

### 対策

#### 1. 正規表現に長さ制限追加

**ファイル**: `app.py`  
**場所**: TAG_RE / DONE_RE 定義

```python
# Before
TAG_RE = re.compile(r"NOTE\(vNext\):\s*(\S+)", re.IGNORECASE)
DONE_RE = re.compile(r"DONE\(vNext\):\s*(\S+)", re.IGNORECASE)

# After
TAG_RE = re.compile(r"NOTE\(vNext\):\s*(\S{1,500})", re.IGNORECASE)
DONE_RE = re.compile(r"DONE\(vNext\):\s*(\S{1,500})", re.IGNORECASE)
```

---

#### 2. DB CHECK制約追加

**ファイル**: `app.py`  
**場所**: `init_db()` の `CREATE TABLE notes` DDL

```python
# Before
CREATE TABLE IF NOT EXISTS notes (
    slug TEXT PRIMARY KEY,
    ...
)

# After
CREATE TABLE IF NOT EXISTS notes (
    slug TEXT PRIMARY KEY CHECK(length(slug) <= 500),
    ...
)
```

---

#### 3. テスト追加

**ファイル**: `tests/test_slug_limits.py`（新規作成）

```python
def test_scan_rejects_slug_over_500_chars(client, tmp_path):
    """Contract: slug must be <= 500 chars."""
    slug = "a" * 501
    p = tmp_path / "long.py"
    p.write_text(f"# NOTE(vNext): {slug}\n", encoding="utf-8")
    
    # scan は成功するが、slug は拾われない（regex で弾かれる）
    r = client.post("/scan?full=0", json={"root": str(tmp_path)})
    assert r.status_code == 200
    
    r2 = client.get("/export/notes")
    slugs = {n["slug"] for n in r2.json()["notes"]}
    assert slug not in slugs

def test_db_rejects_slug_over_500_chars_at_insert(test_db):
    """Contract: DB CHECK constraint must reject slug > 500 chars."""
    from app import db
    con = db()
    try:
        slug = "b" * 501
        con.execute(
            "INSERT INTO notes (slug, status, created_at, updated_at) VALUES (?, 'open', datetime(), datetime())",
            (slug,)
        )
        con.commit()
        assert False, "Should have raised CHECK constraint violation"
    except Exception as e:
        assert "CHECK constraint" in str(e)
    finally:
        con.close()
```

---

### 受け入れ条件

- [ ] 500文字超のslugが正規表現で弾かれる
- [ ] DB CHECK制約で500文字超のslugがINSERT拒否される
- [ ] テストが緑

---

## P1.5-B: SESSION_SECRET 最小長チェック

### 🟡 優先度: 重要（HIGH）
### ⏱ 工数: 15分

### 問題

**現状**:
- `__init__.py` にコメントがあるが未実装
- 短い SESSION_SECRET は HMAC の安全性を損なう

---

### 対策

**ファイル**: `app.py`  
**場所**: `init_settings()` 内

```python
def init_settings():
    global _settings
    # ... 既存のロード処理 ...
    
    # Security: Validate SESSION_SECRET length in prod mode
    if settings["MODE"] == "prod":
        secret = settings.get("SESSION_SECRET", "")
        if len(secret) < 32:
            raise RuntimeError(
                f"SESSION_SECRET must be at least 32 bytes in prod mode (got {len(secret)}). "
                "Generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
    
    _settings = settings
    _init_db()  # P1-4
```

---

### テスト

**ファイル**: `tests/test_session_secret_guard.py`（新規作成）

```python
def test_prod_mode_rejects_short_session_secret(monkeypatch, tmp_path):
    """Contract: prod mode must reject SESSION_SECRET < 32 bytes."""
    import app
    
    old_settings = getattr(app, "_settings", None)
    
    try:
        monkeypatch.setenv("MODE", "prod")
        monkeypatch.setenv("SESSION_SECRET", "short")  # 5 bytes
        monkeypatch.setenv("ADMIN_PASSWORD", "admin")
        monkeypatch.setenv("DEV_PASSWORD", "dev")
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
        
        app._settings = None
        
        with pytest.raises(RuntimeError, match="SESSION_SECRET must be at least 32 bytes"):
            app.init_settings()
    finally:
        app._settings = old_settings

def test_local_mode_allows_any_session_secret(monkeypatch, tmp_path):
    """Contract: local mode must allow any SESSION_SECRET (for dev convenience)."""
    import app
    
    old_settings = getattr(app, "_settings", None)
    
    try:
        monkeypatch.setenv("MODE", "local")
        monkeypatch.setenv("SESSION_SECRET", "x")  # 1 byte OK in local
        
        app._settings = None
        app.init_settings()  # Should not raise
    finally:
        app._settings = old_settings
```

---

### 受け入れ条件

- [ ] prod mode で SESSION_SECRET < 32 bytes → RuntimeError
- [ ] local mode で任意の長さ → OK
- [ ] テストが緑

---

## P1.5-C: scan の encoding error ハンドリング

### 🟡 優先度: 重要（HIGH）
### ⏱ 工数: 15分

### 問題

**現状**:
- `open(file, encoding="utf-8")` でバイナリファイルがクラッシュ
- scan 全体が止まる

---

### 対策

**ファイル**: `app.py`  
**場所**: scan ループ内

```python
for file in files:
    try:
        text = file.read_text(encoding="utf-8")
        # ... 既存の regex 処理 ...
    except (UnicodeDecodeError, PermissionError) as e:
        # Skip files that can't be read as UTF-8
        continue
```

---

### テスト

**ファイル**: `tests/test_scan_encoding_errors.py`（新規作成）

```python
def test_scan_skips_non_utf8_files(client, tmp_path):
    """Contract: scan must skip non-UTF-8 files without crashing."""
    # Create a valid UTF-8 file with NOTE
    valid = tmp_path / "valid.py"
    valid.write_text("# NOTE(vNext): valid_slug\n", encoding="utf-8")
    
    # Create a binary file (will fail UTF-8 decode)
    binary = tmp_path / "binary.pyc"
    binary.write_bytes(b"\x00\xff\xfe\xfd")
    
    # scan should succeed and pick up valid_slug
    r = client.post("/scan?full=0", json={"root": str(tmp_path)})
    assert r.status_code == 200
    
    r2 = client.get("/export/notes")
    slugs = {n["slug"] for n in r2.json()["notes"]}
    assert "valid_slug" in slugs
```

---

### 受け入れ条件

- [ ] バイナリファイルがあっても scan がクラッシュしない
- [ ] UTF-8 ファイルは正常に読める
- [ ] テストが緑

---

## P1-構造化: scan ロジック分割（次点）

### 🟢 優先度: 次点（MEDIUM）
### ⏱ 工数: 3-4時間

**狙い**: 「fullだけ世界を閉じる」規約が将来崩れない

### 手順

#### 1. ScanOutcome dataclass の作成

**ファイル**: `app.py`  
**場所**: ファイル上部

```python
from dataclasses import dataclass

@dataclass
class ScanOutcome:
    files_scanned: int
    slugs_found: int
    evidence_added: int
    done_forced: int
    stale_marked: int
    revived_count: int
    orphan_files_removed: int
    scanned_root: str
    full: bool
```

---

#### 2. 関数分割

**ファイル**: `app.py`  
**場所**: scan セクション

```python
def _run_diff_scan(con: sqlite3.Connection, root: Path, now: str) -> ScanOutcome:
    """
    Diff scan: 新規・更新のみ。
    stale/orphan は絶対走らない。
    """
    files_scanned = 0
    slugs_found = 0
    evidence_added = 0
    done_forced = 0
    
    # ... 既存の diff ロジック ...
    
    return ScanOutcome(
        files_scanned=files_scanned,
        slugs_found=slugs_found,
        evidence_added=evidence_added,
        done_forced=done_forced,
        stale_marked=0,  # ← diff では常に0
        revived_count=0,
        orphan_files_removed=0,  # ← diff では常に0
        scanned_root=str(root),
        full=False,
    )

def _run_full_scan(con: sqlite3.Connection, root: Path, now: str) -> ScanOutcome:
    """
    Full scan: 全体の整合性を保つ。
    stale/orphan を実行する唯一の場所。
    """
    files_scanned = 0
    slugs_found = 0
    evidence_added = 0
    done_forced = 0
    stale_marked = 0
    revived_count = 0
    orphan_files_removed = 0
    
    # ... 既存の full ロジック + stale/orphan ...
    
    return ScanOutcome(
        files_scanned=files_scanned,
        slugs_found=slugs_found,
        evidence_added=evidence_added,
        done_forced=done_forced,
        stale_marked=stale_marked,
        revived_count=revived_count,
        orphan_files_removed=orphan_files_removed,
        scanned_root=str(root),
        full=True,
    )
```

---

#### 3. endpoint の薄化

**ファイル**: `app.py`  
**場所**: `@app.post("/scan")` 関数

```python
@app.post("/scan")
def scan(req: ScanRequest, request: Request):
    # 認証・CSRF（既存）
    # ...
    
    # root 解決（P0-A）
    # ...
    
    with db() as con:
        if req.full:
            outcome = _run_full_scan(con, root_path, now)
        else:
            outcome = _run_diff_scan(con, root_path, now)
        
        # scan_log 記録
        con.execute(
            """
            INSERT INTO scan_log (scanned_root, files_scanned, slugs_found, 
                                  evidence_added, done_forced, stale_marked, 
                                  revived_count, orphan_files_removed, scanned_at, full_scan)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (outcome.scanned_root, outcome.files_scanned, outcome.slugs_found,
             outcome.evidence_added, outcome.done_forced, outcome.stale_marked,
             outcome.revived_count, outcome.orphan_files_removed, now, outcome.full),
        )
        con.commit()
    
    # HTML/JSON 整形
    if _wants_html(request):
        html = render_scan_result(outcome)
        resp = HTMLResponse(html)
        _ensure_csrf_cookie(resp, request)
        return resp
    else:
        return outcome.__dict__
```

---

### 受け入れ条件

- [ ] diff で stale/orphan が絶対走らない
- [ ] full でのみ stale/orphan が走る
- [ ] scan_log の記録内容が従来と一致
- [ ] テストが緑

---

## P1-Repo層: DB アクセスの束ね（次点）

### 🟢 優先度: 次点（MEDIUM）
### ⏱ 工数: 2-3時間

**狙い**: エンドポイント肥大とSQL散乱を止める

**注意**: 過剰抽象化はしない（SQLはそのまま移すだけ）

### 手順

#### 1. NotesRepo の作成

**ファイル**: `app.py`  
**場所**: Database セクション

```python
class NotesRepo:
    def __init__(self, con: sqlite3.Connection):
        self.con = con
    
    def list_all(self, where_sql: str = "", params: tuple = ()) -> list[dict]:
        """全ノート取得"""
        rows = self.con.execute(
            f"""
            SELECT n.id, n.slug, n.status, n.priority, n.created_at, n.updated_at,
                   COUNT(e.id) as evidence_count
            FROM notes n
            LEFT JOIN evidence e ON n.id = e.note_id
            {where_sql}
            GROUP BY n.id
            ORDER BY n.updated_at DESC
            """,
            params
        ).fetchall()
        return [dict(r) for r in rows]
    
    def get_by_slug(self, slug: str) -> Optional[dict]:
        """slug でノート取得"""
        row = self.con.execute(
            "SELECT * FROM notes WHERE slug = ?",
            (slug,)
        ).fetchone()
        return dict(row) if row else None
    
    def update(self, slug: str, status: str, priority: Optional[int], now: str) -> dict:
        """ノート更新"""
        self.con.execute(
            "UPDATE notes SET status = ?, priority = ?, updated_at = ? WHERE slug = ?",
            (status, priority, now, slug)
        )
        row = self.con.execute("SELECT * FROM notes WHERE slug = ?", (slug,)).fetchone()
        return dict(row)
```

---

#### 2. ScanRepo の作成

**ファイル**: `app.py`  
**場所**: Database セクション

```python
class ScanRepo:
    def __init__(self, con: sqlite3.Connection):
        self.con = con
    
    def get_scan_state(self, slug: str) -> Optional[dict]:
        """scan_state 取得"""
        row = self.con.execute(
            "SELECT * FROM scan_state WHERE slug = ?",
            (slug,)
        ).fetchone()
        return dict(row) if row else None
    
    def upsert_scan_state(self, slug: str, status: str, now: str):
        """scan_state 更新・挿入"""
        # ... 既存のロジック ...
    
    def log_scan(self, outcome: ScanOutcome, now: str):
        """scan_log 記録"""
        self.con.execute(
            """
            INSERT INTO scan_log (scanned_root, files_scanned, slugs_found, 
                                  evidence_added, done_forced, stale_marked, 
                                  revived_count, orphan_files_removed, scanned_at, full_scan)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (outcome.scanned_root, outcome.files_scanned, outcome.slugs_found,
             outcome.evidence_added, outcome.done_forced, outcome.stale_marked,
             outcome.revived_count, outcome.orphan_files_removed, now, outcome.full),
        )
```

---

#### 3. endpoint での使用

**ファイル**: `app.py`  
**場所**: エンドポイント関数

```python
@app.get("/export/notes")
def export_notes(request: Request, include_deleted: int = 0, include_archived: int = 0):
    where = []
    params = []
    if not include_deleted:
        where.append("n.is_deleted = 0")
    if not include_archived:
        where.append("n.is_archived = 0")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    
    with db() as con:
        repo = NotesRepo(con)
        notes = repo.list_all(where_sql, tuple(params))
    
    return {"notes": notes}

@app.patch("/notes/{slug}")
def update_note(slug: str, req: UpdateNoteRequest, request: Request):
    # ... CSRF/認証 ...
    
    with db() as con:
        repo = NotesRepo(con)
        
        old_note = repo.get_by_slug(slug)
        if not old_note:
            raise HTTPException(404)
        
        updated = repo.update(slug, req.status, req.priority, now)
        con.commit()
    
    return {"note": updated}
```

---

### 受け入れ条件

- [ ] エンドポイント関数が短くなる
- [ ] SQL の変更箇所が見つけやすくなる
- [ ] テストが緑

---

## P1 完了条件

### 必須（P1 で緑にする）
- [ ] `tests/test_export_and_scan.py` 全て緑
- [ ] `tests/test_patch_notes.py` 全て緑
- [ ] 古いDBで落ちない（カラム補完）
- [ ] `/export/notes` がデフォルトで削除・アーカイブ除外
- [ ] `PATCH /notes/{slug}` が空JSON → 204

### 推奨（P1.5 で緑にする）
- [ ] slug 長さ制限（500文字）
- [ ] SESSION_SECRET 最小長（32 bytes）
- [ ] scan の encoding error ハンドリング

### 次点（構造化・余裕があれば）
- [ ] scan ロジック分割（diff/full）
- [ ] Repo 層導入

---

## 実装順序（推奨）

### Day 1（必須: 2.5h）
1. **P1-1**: 既存DB後方互換（1h）
   - `_ensure_column()` 作成
   - `init_db()` にカラム追加
2. **P1-4**: init_settings→init_db連携（15m）
   - `init_settings()` 末尾に `init_db()` 追加
3. **P1-2**: /export/notes フィルタ（30m）
   - 引数追加
   - WHERE 条件追加
4. **P1-3**: PATCH契約修正（1h）
   - 空JSON判定
   - 返却形式修正
5. **動作確認**
   - `pytest -q tests/test_export_and_scan.py`
   - `pytest -q tests/test_patch_notes.py`

---

### Day 2（推奨: 1h）
6. **P1.5-A**: slug長さ制限（30m）
   - 正規表現修正
   - DB CHECK制約
   - テスト追加
7. **P1.5-B**: SESSION_SECRET長（15m）
   - 起動時チェック
   - テスト追加
8. **P1.5-C**: encoding error（15m）
   - try-except追加
   - テスト追加
9. **動作確認**
   - `pytest -q`

---

### Day 3-4（次点: 5-7h）
10. **P1-構造化**: scan分割（3-4h）
    - ScanOutcome dataclass
    - _run_diff_scan / _run_full_scan
    - endpoint 薄化
11. **P1-Repo層**: Repo導入（2-3h）
    - NotesRepo / ScanRepo
    - endpoint での使用
12. **最終動作確認**
    - `pytest -q`

---

## 動作確認チェックリスト

### Day 1 完了後
```bash
# 契約テスト
pytest -q tests/test_export_and_scan.py
pytest -q tests/test_patch_notes.py

# 古いDBで落ちないか確認
# 1. DBファイルを古いバージョンにする
# 2. アプリ起動
# 3. /scan 実行

# /export/notes フィルタ確認
curl http://localhost:8000/export/notes
# → is_deleted=1 と is_archived=1 が含まれないこと

curl http://localhost:8000/export/notes?include_deleted=1&include_archived=1
# → 全て含まれること

# PATCH 空JSON確認
curl -X PATCH http://localhost:8000/notes/test -H "Content-Type: application/json" -d '{}'
# → 204
```

---

### Day 2 完了後
```bash
# 全テスト
pytest -q

# slug 長さ制限確認
# 1. 500文字超のslugを含むファイルを作成
# 2. /scan 実行
# 3. 拾われないこと確認

# SESSION_SECRET 長確認
MODE=prod SESSION_SECRET=short uvicorn app:app
# → RuntimeError

# encoding error確認
# 1. バイナリファイルを作成
# 2. /scan 実行
# 3. クラッシュしないこと確認
```

---

## トラブルシューティング

### Q1: 古いDBで落ちる

**A1**: `_ensure_column()` が実行されているか確認
```bash
# DBを確認
sqlite3 ledger.sqlite3
PRAGMA table_info(notes);
# → is_deleted, is_archived 等が存在するか

PRAGMA table_info(file_state);
# → mtime_ns, size_bytes 等が存在するか
```

---

### Q2: /export/notes に is_deleted=1 が混ざる

**A2**: WHERE 条件が正しいか確認
```python
# export_notes() 内
print(where_sql)  # デバッグ出力
# → "WHERE n.is_deleted = 0 AND n.is_archived = 0"
```

---

### Q3: PATCH が 200 を返すべきところで 204

**A3**: 空JSON判定が厳しすぎる可能性
```python
# update_note() 内
fields_set = getattr(req, "model_fields_set", set())
print(fields_set)  # デバッグ出力
# → {"status"} 等が含まれているか
```

---

### Q4: テストが fixture不足で落ちる

**A4**: `test_p1_ledger_pollution_contract.py` を退避
```bash
# pytest対象外に移動
mkdir -p tests/_archived
mv tests/test_p1_ledger_pollution_contract.py tests/_archived/
```

---

## 結論

**P1 を完了すれば**:
- ✅ 既存テストの契約が固定される
- ✅ 古いDBとの互換性が保たれる
- ✅ 削除・アーカイブフィルタが動く
- ✅ PATCH の契約が固定される
- ✅ slug長さ制限でDoS防止
- ✅ SESSION_SECRET長でセキュリティ向上
- ✅ encoding errorでクラッシュ防止

**「契約をテストで固定し、将来壊れない構造」が完成します** 🚀

---

## 付録: P1 と P1.5 の関係

### P1（必須）
- 既存テストを緑にする
- 古いDBとの互換性
- 契約固定

### P1.5（推奨）
- 小さな安全性改善
- DoS防止
- セキュリティ向上
- クラッシュ防止

### P1-構造化・Repo層（次点）
- 将来の保守性向上
- リファクタリング
- テスト容易性

**実装順序**: P1 → P1.5 → P1-構造化

---

**P1 実装ガイド完全版 - 以上** 🎉
