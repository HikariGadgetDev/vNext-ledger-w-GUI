# vNext Ledger P0 実装手順書（完全版 - L7精査済み）

## 📋 目的

**「公開デモ・社内配布をしても」セキュリティ・境界・責務で叩かれない最低限の土台**を作る  
（※ 本番運用やスケール最適化はP1以降で扱う）
- 認証/CSRF/scan規約が"抜けない"設計
- prod要件漏れの防止
- 将来のルート追加時の事故防止

---

## 🎯 P0 の範囲と優先度

| 項目 | 優先度 | 工数 | 理由 |
|------|--------|------|------|
| **P0-1: Settings 集約** | 🔴 最重要 | 1-2h | prod要件漏れ防止 |
| **P0-A: /scan root 閉鎖** | 🔴 最重要 | 30m | 任意ディレクトリ参照 |
| **P0-2: Auth gate middleware化** | 🔴 最重要 | 2-3h | 認証抜け防止 |
| **P0-B: CSRF ヘッダ統一** | 🟡 重要 | 30m | 保守性向上 |
| **P0-3: CSRF 発行/検証共通化** | 🟡 重要 | 1-2h | CSRF抜け構造的防止 |
| **P0-C: cookie secure request依存** | 🟡 重要 | 1h | 環境依存事故 |
| **P0-D: Accept判定統一** | 🟢 軽微 | 30m | 美学・保守性 |

**合計工数**: 6.5-10時間（1-2日）

---

## 既に実装済みの項目（前提）

以下は既に実装済み（P0 前に完了している）:

- ✅ **CSRF cookie only enforce**: cookie ある時だけ検証（`_verify_csrf_if_cookie_present`）
- ✅ **full scan safety fuse**: diff で stale/orphan 絶対走らない
- ✅ **commit caller責務**: SQL関数が勝手にcommitしない（呼び出し側がcommit）

**これらは触らない**（既に正しく実装されている）

---

## 共通ルール

### 実装原則
1. **外部仕様は変えない**（エンドポイント・パラメータ・レスポンス形式維持）
2. **commit責務は呼び出し側に置く**（SQL関数が勝手にcommitしない）
3. **変更は小さく分割**（各Pで動作確認を挟む）
4. **diff は最小に保つ**（レビュー容易性）

### 前提
- ローカル運用が主
- ただし **Vercel 等でデモ公開されうる**
- 評価軸: **"境界が閉じているか"** > 重複がないか

---

## P0-1: Settings 集約

### 🔴 優先度: 最重要（CRITICAL）
### ⏱ 工数: 1-2時間

### 問題

**現状**:
```python
# 散在している環境変数読み込み
mode = os.environ.get("MODE", "local")
admin_password = os.environ.get("ADMIN_PASSWORD")
session_secret = os.environ.get("SESSION_SECRET")
# ... 複数箇所に分散
```

**問題点**:
- prod要件漏れ（必須env未設定で起動してしまう）
- モード分岐が散乱
- テスト時の差し替えが困難

---

### 対策

#### 1. Settings クラスの作成

**ファイル**: `app.py`  
**場所**: ファイル上部（import の直後）

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class Settings:
    mode: str  # "local" or "prod"
    admin_password: Optional[str]
    dev_password: Optional[str]
    session_secret: str
    repo_root: Optional[str]
    allow_local_json_noauth: bool
    cookie_secure: bool  # prodならTrue

def load_settings() -> Settings:
    """
    環境変数から設定を読み込む。
    prodモードで必須env欠落時は例外を投げる。
    """
    mode = os.environ.get("MODE", "local").lower()
    admin_password = os.environ.get("ADMIN_PASSWORD")
    dev_password = os.environ.get("DEV_PASSWORD")
    session_secret = os.environ.get("SESSION_SECRET")
    repo_root = os.environ.get("LEDGER_REPO_ROOT")
    
    # prodモード必須チェック
    if mode == "prod":
        if not admin_password:
            raise RuntimeError("ADMIN_PASSWORD is required in prod mode")
        if not session_secret:
            raise RuntimeError("SESSION_SECRET is required in prod mode")
    
    # localモードのデフォルト
    if mode == "local":
        if not session_secret:
            session_secret = "local-dev-secret-DO-NOT-USE-IN-PROD"
        if not dev_password:
            dev_password = "dev"
    
    return Settings(
        mode=mode,
        admin_password=admin_password,
        dev_password=dev_password,
        session_secret=session_secret,
        repo_root=repo_root,
        allow_local_json_noauth=(mode == "local"),
        cookie_secure=(mode == "prod"),
    )

# グローバル変数（startup時に初期化）
_settings: Optional[Settings] = None

def get_settings() -> Settings:
    """設定を取得。未初期化なら例外。"""
    if _settings is None:
        raise RuntimeError("Settings not initialized. Call load_settings() first.")
    return _settings
```

---

#### 2. lifespan での初期化

**ファイル**: `app.py`  
**場所**: `@asynccontextmanager` 内

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    global _settings
    _settings = load_settings()  # ← ここで読み込み（起動時バリデーション）
    _init_db()
    yield
    # shutdown
    # ... 既存の処理
```

---

#### 3. 既存コードの置き換え

**ファイル**: `app.py`  
**場所**: 全体

```python
# Before
mode = os.environ.get("MODE", "local")
if mode == "prod":
    # ...

# After
s = get_settings()
if s.mode == "prod":
    # ...
```

**対象箇所**:
- 認証チェック
- cookie secure 判定
- CSRF 判定
- scan root 解決
- その他すべての環境変数アクセス

---

### 受け入れ条件

- [ ] `MODE=prod` で `ADMIN_PASSWORD` 未設定時に起動失敗
- [ ] `MODE=prod` で `SESSION_SECRET` 未設定時に起動失敗
- [ ] `MODE=local` で従来どおり動く
- [ ] コード内で `os.environ.get(...)` を直接触らない

---

## P0-A: /scan の root を"閉じる"

### 🔴 優先度: 最重要（CRITICAL）
### ⏱ 工数: 30分

### 問題

**現状**:
```python
@app.post("/scan")
def scan(req: ScanRequest):
    root_path = resolve_root(req.root)  # ← req.root をそのまま使用
    # ...
```

**セキュリティホール**:
```bash
# 任意ディレクトリ参照が可能
POST /scan { "root": "/etc" }
POST /scan { "root": "/home/user/.ssh" }
```

- 認証があっても **サーバ上の任意ディレクトリ参照**は事故の芽
- **Google的に一番揉まれる本丸**

---

### 対策（3つの選択肢）

#### 選択肢1: MODE=prod では req.root を完全に無視（推奨）

**ファイル**: `app.py`  
**場所**: `scan()` 関数内

```python
# Before
root_path = resolve_root(req.root)

# After
s = get_settings()
if s.mode == "prod":
    root_path = resolve_root(None)  # env/auto-detect/fallbackのみ
else:
    root_path = resolve_root(req.root)  # localは利便性維持
```

**理由**:
- prod は env固定（`LEDGER_REPO_ROOT`）
- ローカルの開発体験は維持
- 最小の変更

---

#### 選択肢2: allowlist 検証（より厳密）

**ファイル**: `app.py`  
**場所**: `scan()` 関数内

```python
# After
root_path = resolve_root(req.root)

# 検証: LEDGER_REPO_ROOT 配下のみ許可
s = get_settings()
allowed_root = Path(s.repo_root or DEFAULT_REPO_ROOT).resolve()
if not root_path.is_relative_to(allowed_root):
    raise HTTPException(
        status_code=400,
        detail=f"root must be under {allowed_root}"
    )
```

**理由**:
- ディレクトリトラバーサル完全防止
- `../../../etc/passwd` も防げる

---

#### 選択肢3: API から root 削除（最も厳密）

**ファイル**: `app.py`  
**場所**: `ScanRequest` 定義

```python
# Before
class ScanRequest(BaseModel):
    root: Optional[str] = None
    full: bool = False

# After
class ScanRequest(BaseModel):
    # root を削除
    full: bool = False
```

```python
# scan() 内
root_path = resolve_root(None)  # 常に env/auto-detect
```

**理由**:
- 最もシンプル（選択肢がない＝安全）
- UI/運用は env 固定

---

### 推奨

**今すぐ**: 選択肢1（最小変更）  
**P1**: 選択肢2 or 3（より厳密化）

---

### 受け入れ条件

- [ ] prod モードで `req.root` が無視される
- [ ] local モードで従来どおり動く
- [ ] `/etc` 等への参照が防げる
- [ ] scan_log に記録される root が正しい

---

## P0-2: Auth gate を middleware 1本に固定

### 🔴 優先度: 最重要（CRITICAL）
### ⏱ 工数: 2-3時間

### 問題

**現状**:
```python
# 各エンドポイントに分散
@app.get("/notes/{slug}")
def get_note(slug: str, request: Request):
    _ensure_role(request, {"admin", "dev"})  # ← 分散
    # ...

@app.post("/scan")
def scan(req: ScanRequest, request: Request):
    _ensure_role(request, {"admin", "dev"})  # ← 分散
    # ...
```

**問題点**:
- ルート追加時の「認証抜け」
- 逆に過剰ブロック（public経路を間違えてブロック）

---

### 対策

#### 1. 判定関数の作成

**ファイル**: `app.py`  
**場所**: middleware セクション

```python
def _is_public_path(path: str) -> bool:
    """認証不要な経路"""
    public_paths = {
        "/",           # UI トップ
        "/login",      # ログイン画面
        "/logout",     # ログアウト
        "/health",     # ヘルスチェック
    }
    if path in public_paths:
        return True
    if path.startswith("/static/"):
        return True
    return False

def _is_localhost(request: Request) -> bool:
    """localhost判定"""
    client_host = (request.client.host if request.client else "") or ""
    return client_host in {"127.0.0.1", "::1"}

def _wants_json_only(request: Request) -> bool:
    """JSON API判定"""
    accept = (request.headers.get("accept") or "").lower()
    return "application/json" in accept

def _require_auth(request: Request) -> Optional[Response]:
    """
    認証が必要ならチェック。
    未ログインなら /login へリダイレクト（HTML）or 401（JSON）。
    """
    s = get_settings()
    
    # local JSON noauth 許可
    if s.allow_local_json_noauth and _is_localhost(request) and _wants_json_only(request):
        return None
    
    # セッション検証
    session_token = request.cookies.get("session_token")
    if not session_token:
        # 未ログイン
        if _wants_json_only(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        else:
            return RedirectResponse("/login", status_code=303)
    
    # セッション有効性チェック
    con = db()
    try:
        row = con.execute(
            "SELECT uid FROM sessions WHERE token = ? AND expires_at > ?",
            (session_token, datetime.now().isoformat())
        ).fetchone()
        if not row:
            # セッション無効
            if _wants_json_only(request):
                return JSONResponse({"error": "Session expired"}, status_code=401)
            else:
                return RedirectResponse("/login", status_code=303)
    finally:
        con.close()
    
    return None  # 認証OK
```

---

#### 2. middleware の追加

**ファイル**: `app.py`  
**場所**: middleware セクション

```python
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """認証チェック middleware"""
    
    # public経路は素通し
    if _is_public_path(request.url.path):
        return await call_next(request)
    
    # 認証チェック
    auth_response = _require_auth(request)
    if auth_response:
        return auth_response
    
    # 認証OK → 続行
    return await call_next(request)
```

---

#### 3. 既存エンドポイントからの削除

**ファイル**: `app.py`  
**場所**: 全エンドポイント

```python
# Before
@app.get("/notes/{slug}")
def get_note(slug: str, request: Request):
    _ensure_role(request, {"admin", "dev"})  # ← 削除
    # ...

# After
@app.get("/notes/{slug}")
def get_note(slug: str, request: Request):
    # 認証チェックは middleware が行う
    # ...
```

**対象エンドポイント**:
- `/notes/{slug}`
- `/scan`
- `/export/*`
- その他すべての非public経路

---

### 受け入れ条件

- [ ] public経路（`/`, `/login`, `/static/*`）は常に通る
- [ ] 非publicで未ログインは `/login` リダイレクト（HTML）or 401（JSON）
- [ ] local JSON noauth 許可条件が正しく動く
- [ ] 将来ルート追加時、middleware だけ触れば安全

---

## P0-B: CSRF ヘッダ名の統一定数化

### 🟡 優先度: 重要（HIGH）
### ⏱ 工数: 30分

### 問題

**現状**:
```python
# 文字列直書きが3箇所に分散
def _verify_csrf_if_cookie_present(request: Request):
    header_token = request.headers.get("x-csrf-token")  # ← 直書き

# render_note_modal()
hx-headers='{"x-csrf-token": "..."}'  # ← 直書き

# ui.js
evt.detail.headers["x-csrf-token"] = token;  # ← 直書き
```

**問題点**:
- ヘッダ名変更時に3箇所修正が必要
- 探索コストが増える

---

### 対策

#### 1. 定数の定義

**ファイル**: `app.py`  
**場所**: ファイル上部（Settings の直後）

```python
# CSRF 定数
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"
```

---

#### 2. 既存コードの置き換え

**ファイル**: `app.py`  
**場所**: 全体

```python
# Before
def _verify_csrf_if_cookie_present(request: Request):
    cookie_token = request.cookies.get("csrf_token")
    header_token = request.headers.get("x-csrf-token")
    # ...

def _ensure_csrf_cookie(response: Response, request: Request) -> str:
    existing = request.cookies.get("csrf_token")
    # ...
    response.set_cookie(key="csrf_token", ...)
    # ...

# After
def _verify_csrf_if_cookie_present(request: Request):
    cookie_token = request.cookies.get(CSRF_COOKIE)
    header_token = request.headers.get(CSRF_HEADER)
    # ...

def _ensure_csrf_cookie(response: Response, request: Request) -> str:
    existing = request.cookies.get(CSRF_COOKIE)
    # ...
    response.set_cookie(key=CSRF_COOKIE, ...)
    # ...
```

---

#### 3. render.py の修正

**ファイル**: `render.py`  
**場所**: `render_note_modal()`

```python
# Before
hx-headers='{"x-csrf-token": "{token}"}'

# After
# app.py から定数をインポート
from app import CSRF_HEADER

hx-headers='{{"{header}": "{token}"}}'.format(header=CSRF_HEADER, token=token)
```

---

#### 4. ui.js の修正

**ファイル**: `static/ui.js`  
**場所**: CSRF 注入部分

```javascript
// Before
const token = getCookie("csrf_token");
evt.detail.headers["x-csrf-token"] = token;

// After
const CSRF_COOKIE = "csrf_token";  // app.py と一致
const CSRF_HEADER = "x-csrf-token";  // app.py と一致

const token = getCookie(CSRF_COOKIE);
evt.detail.headers[CSRF_HEADER] = token;
```

**注意**: JS には Python の定数を直接渡せないので、文字列を複製

---

### 受け入れ条件

- [ ] `CSRF_COOKIE` と `CSRF_HEADER` が定数化
- [ ] すべての箇所で定数を使用
- [ ] ヘッダ名変更時に定数1箇所の修正で済む

---

## P0-3: CSRF 発行/検証の共通化

### 🟡 優先度: 重要（HIGH）
### ⏱ 工数: 1-2時間

**重要**: これは P0-B（定数化）とは別の項目です。

### 狙い

HTMLルート増加で「csrf cookie未発行」→「謎403」事故を構造的に防止

---

### 問題

**現状**:
```python
# HTMLレスポンスを返す箇所が分散
@app.get("/")
def ui_index():
    return HTMLResponse(html)  # ← csrf_token cookie 付与忘れの可能性

@app.get("/notes/table")
def notes_table():
    if _wants_html(request):
        return HTMLResponse(...)  # ← 同上
```

**問題点**:
- 新しいHTMLルート追加時に cookie 付与を忘れる
- 403 が頻発して原因不明

---

### 対策

#### 1. ensure_csrf_cookie() の作成

**ファイル**: `app.py`  
**場所**: CSRF セクション

```python
def _ensure_csrf_cookie(response: Response, request: Request) -> str:
    """
    CSRF cookie が未発行なら発行する。
    トークン文字列を返す。
    
    ⚠️ 重要: HTMLレスポンスを返す場所で必ず呼ぶこと。
    """
    existing = request.cookies.get(CSRF_COOKIE)
    if existing:
        return existing
    
    # 新規発行
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        httponly=True,
        secure=_should_secure_cookie(request),  # P0-C で実装
        samesite="lax",
    )
    return token
```

**注意**: `_should_secure_cookie()` は P0-C で実装（まだない場合は `s.cookie_secure` を使用）

---

#### 2. HTMLレスポンスを返す全箇所に適用

**ルール**: `HTMLResponse` または `FileResponse` を返す直前に必ず1行呼ぶ

---

##### A. `/` (UI トップ)

**ファイル**: `app.py`  
**場所**: `ui_index()` 関数

```python
# Before
@app.get("/")
def ui_index(request: Request):
    html = render_ui_index()
    return HTMLResponse(html)

# After
@app.get("/")
def ui_index(request: Request):
    html = render_ui_index()
    resp = HTMLResponse(html)
    _ensure_csrf_cookie(resp, request)  # ← 追加
    return resp
```

---

##### B. `/notes/table` (HTML 分岐)

**ファイル**: `app.py`  
**場所**: `notes_table()` 関数

```python
# Before
if _wants_html(request):
    resp = HTMLResponse(render_notes_table(notes))
    return resp

# After
if _wants_html(request):
    resp = HTMLResponse(render_notes_table(notes))
    _ensure_csrf_cookie(resp, request)  # ← 追加
    return resp
```

---

##### C. `/notes/{slug}` (HTML 分岐)

**ファイル**: `app.py`  
**場所**: `get_note()` 関数

```python
# Before
if _wants_html(request):
    html = render_note_detail(note, evidence, events)
    return HTMLResponse(html)

# After
if _wants_html(request):
    html = render_note_detail(note, evidence, events)
    resp = HTMLResponse(html)
    _ensure_csrf_cookie(resp, request)  # ← 追加
    return resp
```

---

##### D. `/scan` (HTML 分岐)

**ファイル**: `app.py`  
**場所**: `scan()` 関数

```python
# Before
if _wants_html(request):
    html = render_scan_result(outcome)
    return HTMLResponse(html)

# After
if _wants_html(request):
    html = render_scan_result(outcome)
    resp = HTMLResponse(html)
    _ensure_csrf_cookie(resp, request)  # ← 追加
    return resp
```

---

##### E. `/export/*` (HTML 分岐)

**ファイル**: `app.py`  
**場所**: すべての export エンドポイント

```python
# 同様のパターンで適用
if _wants_html(request):
    html = render_something(...)
    resp = HTMLResponse(html)
    _ensure_csrf_cookie(resp, request)  # ← 追加
    return resp
```

---

#### 3. 将来のルート追加時のルール

**コードレビューチェックリスト**:
```python
# ❌ NG: cookie 付与忘れ
@app.get("/new-feature")
def new_feature(request: Request):
    html = render_something()
    return HTMLResponse(html)  # ← csrf_token cookie が付かない → 403

# ✅ OK: 必ず1行呼ぶ
@app.get("/new-feature")
def new_feature(request: Request):
    html = render_something()
    resp = HTMLResponse(html)
    _ensure_csrf_cookie(resp, request)  # ← 必須
    return resp
```

**チェックポイント**:
- HTMLResponse を返す箇所
- FileResponse を返す箇所（静的HTMLファイル）
- ensure_csrf_cookie() が呼ばれているか

---

### 受け入れ条件

- [ ] `_ensure_csrf_cookie()` 関数が作成されている
- [ ] すべての HTMLResponse 箇所で呼ばれている（A-E）
- [ ] 将来のルート追加時のルールが明確化されている
- [ ] UIアクセスで必ず `csrf_token` cookie が付与される
- [ ] HTMX操作が安定して403にならない
- [ ] curl/CIのJSON呼び出しはcookie無しなら邪魔されない（現方針維持）

---

## P0-C: cookie secure を request 依存に

### 🟡 優先度: 重要（MEDIUM）
### ⏱ 工数: 1時間

### 問題

**現状**:
```python
# Settings で決め打ち
cookie_secure = (s.mode == "prod")
```

**問題点**:
- prod だが app には http に見える構成（例: Vercel, nginx reverse proxy）
- secure cookie が付かずログイン不能事故

---

### 対策

#### 1. request 判定関数の追加

**ファイル**: `app.py`  
**場所**: ヘルパー関数セクション

```python
def _is_https(request: Request) -> bool:
    """HTTPS判定"""
    # X-Forwarded-Proto をチェック（reverse proxy対応）
    proto = request.headers.get("x-forwarded-proto", "").lower()
    if proto == "https":
        return True
    
    # request.url.scheme をチェック
    if request.url.scheme == "https":
        return True
    
    return False

def _should_secure_cookie(request: Request) -> bool:
    """cookie に secure 属性を付けるべきか"""
    s = get_settings()
    return (s.mode == "prod") and _is_https(request)
```

---

#### 2. cookie 設定箇所の修正

**ファイル**: `app.py`  
**場所**: `_set_session_cookie()`, `_ensure_csrf_cookie()`

```python
# Before
def _set_session_cookie(response: Response, token: str):
    s = get_settings()
    response.set_cookie(
        key="session_token",
        value=token,
        secure=s.cookie_secure,  # ← Settings 依存
        httponly=True,
        samesite="lax",
    )

# After
def _set_session_cookie(response: Response, request: Request, token: str):
    response.set_cookie(
        key="session_token",
        value=token,
        secure=_should_secure_cookie(request),  # ← request 依存
        httponly=True,
        samesite="lax",
    )
```

**注意**: 関数シグネチャに `request: Request` を追加

---

#### 3. 呼び出し側の修正

**ファイル**: `app.py`  
**場所**: `/login` エンドポイント

```python
# Before
_set_session_cookie(resp, token)

# After
_set_session_cookie(resp, request, token)
```

---

### 受け入れ条件

- [ ] prod + https → secure=True
- [ ] prod + http → secure=False（reverse proxy 経由の場合）
- [ ] local → secure=False
- [ ] Vercel 等でログイン可能

---

## P0-D: Accept 判定を Request に一本化

### 🟢 優先度: 軽微（LOW）
### ⏱ 工数: 30分

### 問題

**現状**:
```python
# accept 引数が分散
@app.post("/scan")
def scan(req: ScanRequest, request: Request, accept: str = Header(default="*/*")):
    # ...

@app.get("/notes/{slug}")
def get_note(slug: str, request: Request, accept: str = Header(default="*/*")):
    # ...
```

**問題点**:
- FastAPI 的には Request を見るのが自然
- 引数が増える

---

### 対策

#### 1. accept 引数の削除

**ファイル**: `app.py`  
**場所**: 各エンドポイント

```python
# Before
@app.post("/scan")
def scan(req: ScanRequest, request: Request, accept: str = Header(default="*/*")):
    if _wants_html(request, accept):
        # ...

# After
@app.post("/scan")
def scan(req: ScanRequest, request: Request):
    if _wants_html(request):
        # ...
```

---

#### 2. _wants_html() の修正

**ファイル**: `app.py`  
**場所**: ヘルパー関数セクション

```python
# Before
def _wants_html(request: Request, accept: str) -> bool:
    # accept 引数を使用
    # ...

# After
def _wants_html(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    # 既存のロジック
    # ...
```

---

### 受け入れ条件

- [ ] accept 引数がすべて削除される
- [ ] `_wants_html(request)` で統一
- [ ] 動作が従来と同じ

---

## P0 完了条件

### 既に実装済み（前提）
- ✅ CSRF cookie only enforce（`_verify_csrf_if_cookie_present`）
- ✅ full scan safety fuse（diff で stale/orphan 走らない）
- ✅ commit caller責務（呼び出し側がcommit）

### P0 で実装
- [ ] Settings single source（P0-1）
- [ ] scan root 閉鎖（P0-A）
- [ ] Auth gate middleware化（P0-2）
- [ ] CSRF ヘッダ統一（P0-B）
- [ ] CSRF 発行/検証共通化（P0-3）
- [ ] cookie secure request依存（P0-C）
- [ ] Accept判定統一（P0-D）

**すべて満たせば「公開して叩かれない」** 🚀

---

## 実装順序（推奨）

### Day 1（最重要）
1. **P0-1**: Settings 集約（1-2時間）
   - lifespan で load_settings()
   - 起動時バリデーション
2. **P0-A**: /scan root 閉鎖（30分）
   - 選択肢1（prod で無視）を実装
3. **動作確認**
   - MODE=prod で必須env欠落時に起動しない
   - prod で scan root が閉じている

---

### Day 2（重要）
4. **P0-2**: Auth gate middleware化（2-3時間）
   - _require_auth() 作成
   - middleware 追加
   - エンドポイントから認証削除
5. **P0-B**: CSRF ヘッダ統一（30分）
   - CSRF_COOKIE, CSRF_HEADER 定数化
   - 全箇所置き換え
6. **動作確認**
   - 認証なしで非public経路が弾かれる
   - public経路は通る

---

### Day 3（余力）
7. **P0-3**: CSRF 発行/検証共通化（1-2時間）
   - _ensure_csrf_cookie() 作成
   - 全HTMLレスポンス箇所に適用
8. **P0-C**: cookie secure request依存（1時間）
   - _should_secure_cookie() 作成
   - cookie設定箇所修正
9. **P0-D**: Accept判定統一（30分）
   - accept 引数削除
10. **最終動作確認**

---

## 動作確認チェックリスト

### ローカル環境
```bash
# 1. 起動
MODE=local uvicorn app:app --reload

# 2. ブラウザで http://localhost:8000
# → CSRF cookie が付く

# 3. HTMX 操作（scan / modal）
# → 403 にならない

# 4. curl でJSON API
curl -H "Accept: application/json" http://localhost:8000/notes/table
# → 認証なしで動く（local noauth許可）
```

---

### prod 環境
```bash
# 1. 必須env未設定で起動
MODE=prod uvicorn app:app
# → RuntimeError: ADMIN_PASSWORD is required

# 2. 必須env設定で起動
MODE=prod ADMIN_PASSWORD=xxx SESSION_SECRET=yyy uvicorn app:app
# → 起動成功

# 3. /scan の root が閉じている
POST /scan {"root": "/etc"}
# → env固定で無視される

# 4. 認証なしでアクセス
curl http://your-domain.com/notes/table
# → 401 or /login リダイレクト
```

---

### full scan 挙動確認（重要）

**これが最も重要**: stale/orphan 処理が正しく制御されているか

```bash
# 4. full=False で stale/orphan が一切発生しない
POST /scan {"full": false}

# ログ確認
cat logs/app.log | grep -E "stale|orphan"
# → 出力なし（diff scan では stale/orphan 処理が走らない）

# 5. full=True で stale/orphan が発生しうる
POST /scan {"full": true}

# ログ確認
cat logs/app.log | grep -E "stale|orphan"
# → stale marked: 3 files
# → orphan removed: 2 files
```

**重要性**:
- diff scan で stale/orphan が走ると重要なノートが消える
- これは P0 の「full scan safety fuse」の本質
- 必ず確認すること

---

## トラブルシューティング

### Q1: Settings が未初期化エラー

**A1**: lifespan で `load_settings()` を呼んでいるか確認
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _settings
    _settings = load_settings()  # ← これがないとエラー
    # ...
```

---

### Q2: CSRF 403 が頻発

**A2**: 
1. CSRF cookie が付与されているか確認（DevTools → Application → Cookies）
2. ui.js が CSRF ヘッダを注入しているか確認（DevTools → Network → Headers）
3. HTMLレスポンスで `_ensure_csrf_cookie()` を呼んでいるか確認
4. CSRF_COOKIE と CSRF_HEADER が一致しているか確認

---

### Q3: prod でログインできない

**A3**: 
1. `_should_secure_cookie()` が正しく判定しているか確認
2. reverse proxy が `X-Forwarded-Proto: https` を送っているか確認
3. secure cookie が設定されているか確認（DevTools → Application → Cookies）

---

### Q4: full scan で stale/orphan が走らない

**A4**: 
1. `full=true` を指定しているか確認
2. ログレベルが適切か確認
3. scan_log テーブルを確認（full が true で記録されているか）

---

### Q5: diff scan で stale/orphan が走ってしまう

**A5**: 
1. **これは重大なバグ**（P0 の本質が壊れている）
2. scan() 関数の分岐を確認
3. `if req.full:` の条件が正しいか確認

---

## 結論

**P0 を完了すれば**:
- ✅ 任意ディレクトリ参照を防げる
- ✅ prod要件漏れを防げる
- ✅ 認証抜けを構造的に防げる
- ✅ CSRF が統一・共通化される
- ✅ 環境依存事故を防げる
- ✅ full scan safety fuse が確認される

**「公開して叩かれない」状態になります** 🚀

---

## 付録: P0 の設計思想

### なぜ P0-3 は P0-B と別か

**P0-B（定数化）**:
- 既存の仕組みを壊さない
- 文字列を定数に置き換えるだけ
- 30分で完了

**P0-3（発行/検証の共通化）**:
- 新しいルール（ensure_csrf_cookie）を導入
- 全HTMLレスポンス箇所に適用
- 1-2時間かかる

**分離する理由**:
- レビューしやすい
- ロールバックしやすい
- 依存関係が明確（P0-B → P0-3）

---

### なぜ full scan 挙動確認が重要か

**理由**:
- diff scan で stale/orphan が走ると**重要なノートが消える**
- これは P0 の「full scan safety fuse」の本質
- テストでは検出できない（実データで確認必要）

**確認方法**:
- ログで確認（grep -E "stale|orphan"）
- scan_log テーブルで確認
- 実際に diff/full を実行して挙動を見る

**これを怠ると**: 本番で重要なノートが消える事故

---

**P0 実装ガイド完全版 - 以上** 🎉
