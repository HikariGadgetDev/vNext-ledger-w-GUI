# P2-EX 改善提案ログ（後でやること）

## 🚨 重要な前提

これらの提案は**P1.6では実装しない**

理由:
1. **リファクタ禁止原則**（P1は契約固定＆最小差分）
2. **機能追加扱い**（P1範囲外）
3. **テスト拡充前提**（別フェーズで）

---

## P2-EX-01: フィルタロジック分離 ⭐⭐⭐

### 目的
- テスト容易性向上
- SRP準拠（Single Responsibility Principle）
- 再利用性向上

### 提案内容

```python
def _build_notes_filter(
    status: Optional[str],
    priority: Optional[str],
    comment: Optional[str],
) -> tuple[str, list[object]]:
    """
    Build WHERE clause and params for notes filtering.
    
    Returns:
        (where_sql, params) tuple
        
    Contract:
        - Returns empty string if no filters
        - Always uses parameterized queries (SQL injection safe)
        - Validates all inputs before building SQL
        
    Safety:
        - WHERE句断片は定数文字列のみ
        - プレースホルダー数は len() で決定
        - 値は params で渡す（パラメータ化維持）
    """
    where: list[str] = []
    params: list[object] = []
    
    # Status filter
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        for s in statuses:
            if s not in ALLOWED_STATUS:
                raise HTTPException(status_code=400, detail="Invalid status filter")
        where.append("n.status IN (%s)" % ",".join(["?"] * len(statuses)))
        params.extend(statuses)
    
    # Priority filter (same as P1.6)
    # ...
    
    # Comment filter (same as P1.6)
    # ...
    
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    return where_sql, params
```

### エンドポイント簡略化

```python
@app.get("/notes/table")
def notes_table(
    request: Request,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    comment: Optional[str] = None,
):
    """Get filtered notes table."""
    if not (_no_auth_json_exception() and _is_local_host(request) and _wants_json(request)):
        _ensure_role(request, {"admin", "dev"})
    
    # 🎯 フィルタロジックを分離
    where_sql, params = _build_notes_filter(status, priority, comment)
    
    with db() as con:
        rows = con.execute(
            f"""
            SELECT n.id, n.slug, n.status, n.priority, n.created_at, n.updated_at,
                   COUNT(e.id) AS evidence_count
            FROM notes n
            LEFT JOIN evidence e ON n.id = e.note_id
            {where_sql}
            GROUP BY n.id
            ORDER BY n.priority DESC, n.updated_at DESC
            """,
            tuple(params),
        ).fetchall()
    
    notes = [dict(r) for r in rows]
    
    # Response logic (same as P1.6)
    # ...
```

### メリット
- `notes_table()` が約50行→約30行に削減
- `_build_notes_filter()` を単体テスト可能
- 他のエンドポイントでも再利用可能

### 実装前の要件
1. **単体テスト作成**
   ```python
   def test_build_notes_filter_priority_none():
       where_sql, params = _build_notes_filter(None, "none", None)
       assert "n.priority IS NULL" in where_sql
       assert params == []
   ```

2. **セキュリティ検証**
   - WHERE句断片が定数のみであることを確認
   - プレースホルダー数が正しいことを確認
   - パラメータ化が維持されていることを確認

3. **回帰テスト**
   - 既存の `test_notes_table_filters_priority_and_comment()` が全てパス

### ⚠️ 注意点

#### 「盛ってる」ポイント
```python
# ❌ P1.6範囲外の機能追加が含まれている
if status:
    statuses = [s.strip() for s in status.split(",") if s.strip()]
    # ↑ これは ?status=open,done みたいな複数指定対応
    # P1.6では status は単一値のみ
```

**対応**: P2-EX-01では「複数指定対応」を明示的に機能追加として扱う

#### SQL安全性の根拠を明文化
```python
"""
Safety guarantee:
1. WHERE clause fragments are constant strings only
   - "n.status IN (%s)" % ",".join(["?"] * len(statuses))
   - プレースホルダー数は len(statuses) で決定（ユーザー入力なし）

2. Values are passed via parameterized queries
   - params.extend(statuses)
   - SQLiteが適切にエスケープ

3. Input validation before SQL construction
   - if s not in ALLOWED_STATUS: raise HTTPException(400)
   - 不正な値は SQL 到達前に弾く
"""
```

---

## P2-EX-02: PATCH処理構造化 ⭐⭐

### 目的
- 変更検出ロジックの明確化
- テスト容易性向上
- 契約の明示化

### ⚠️ 危険ポイント（このまま実装すると事故る）

#### 問題1: UPDATE で None 混入の危険

```python
# ❌ DANGEROUS: 提案サンプルの問題点
def _apply_note_changes(con, note_id, change, now):
    if change.status_changed or change.priority_changed:
        con.execute(
            "UPDATE notes SET status = ?, priority = ?, updated_at = ? WHERE id = ?",
            (
                change.new_status if change.status_changed else None,  # ⚠️ ここ！
                change.new_priority if change.priority_changed else None,  # ⚠️ ここ！
                now,
                note_id,
            ),
        )
```

**何が危険か**:
- `change.status_changed == False` の時、`status = None` になる
- 意図せず status を NULL にクリアしてしまう

**正しい実装**:
```python
# ✅ SAFE: 変更があった列だけUPDATE
if change.status_changed:
    con.execute(
        "UPDATE notes SET status = ?, updated_at = ? WHERE id = ?",
        (change.new_status, now, note_id),
    )

if change.priority_changed:
    con.execute(
        "UPDATE notes SET priority = ?, updated_at = ? WHERE id = ?",
        (change.new_priority, now, note_id),
    )
```

または:
```python
# ✅ SAFE: SET句を動的に構築（安全に）
updates = []
params = []

if change.status_changed:
    updates.append("status = ?")
    params.append(change.new_status)

if change.priority_changed:
    updates.append("priority = ?")
    params.append(change.new_priority)

if updates:
    updates.append("updated_at = ?")
    params.append(now)
    params.append(note_id)
    
    con.execute(
        f"UPDATE notes SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )
```

#### 問題2: comment の null 扱いが変わる

```python
# 現状（P1.6）
if comment_provided and req.comment is None:
    raise HTTPException(status_code=400, detail="Invalid comment")

# 提案サンプル
if comment_provided and req.comment is not None:
    change.comment_added = True
    # ↑ これだと {"comment": null} が 400 にならない可能性
```

**対応**: 挙動変更は明示的に契約化してからテスト追加

### 正しい実装フロー

1. **Phase 1: 契約を明確化**
   ```python
   """
   PATCH処理の契約:
   
   1. Empty PATCH ({}) → 204 (no-op)
   2. Same value PATCH → 204 (no-op, 監査ログ汚染防止)
   3. {"status": null} → 400 (Invalid status)
   4. {"priority": null} → 200 (クリア成功、NULL許可)
   5. {"comment": null} → 400 (Invalid comment)
   """
   ```

2. **Phase 2: テスト先行**
   ```python
   def test_patch_update_only_changed_columns():
       """UPDATE は変更された列のみを更新すること"""
       note_id = insert_note(slug="test", status="open", priority=1)
       
       # priority のみ変更
       r = client.patch("/notes/test", json={"priority": 2})
       assert r.status_code == 200
       
       # DB確認: status は変更されていない
       row = get_note(note_id)
       assert row["status"] == "open"  # 維持
       assert row["priority"] == 2     # 変更
   ```

3. **Phase 3: 実装**
   - `_compute_note_changes()` 実装
   - `_apply_note_changes()` 実装（安全なUPDATE）
   - テスト実行

### メリット（正しく実装できれば）
- 変更検出ロジックが明確
- テストしやすい
- 契約が一箇所に集約

### 実装前の要件
1. 契約の明文化
2. テスト拡充（UPDATE列の検証含む）
3. セキュリティレビュー

---

## P2-EX-03: テストヘルパー共通化 ⭐

### 目的
- DRY原則
- テストの可読性向上

### 提案内容

```python
# tests/helpers/db_fixtures.py

def insert_note(
    *,
    slug: str,
    status: str = "open",
    priority: Optional[int] = None,
) -> int:
    """Insert a note and return its ID (idempotent)."""
    now = datetime.now().isoformat(timespec="seconds")
    con = db()
    try:
        # Idempotent: delete first
        con.execute("DELETE FROM notes WHERE slug = ?", (slug,))
        
        cur = con.execute(
            """
            INSERT INTO notes (
                slug, status, priority, created_at, updated_at,
                first_seen, last_seen, evidence_count,
                is_deleted, deleted_at, is_archived, archived_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, NULL, 0, NULL)
            """,
            (slug, status, priority, now, now, now, now),
        )
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()
```

### ⚠️ 注意点

#### 「パッチ以外のリファクタ禁止」に触れる

- P1.6では `test_table_filters.py` 内に `_insert_note()` がある
- これを `tests/helpers/` に移動すると「リファクタ」扱いになる

**対応**: P2-EXで移動する際は:
1. 既存テストが全てパス
2. 新規ヘルパーのテスト追加
3. 段階的移行（一度に全部変えない）

### メリット
- テストコードの重複削減
- テストの可読性向上
- ヘルパー自体をテスト可能

### 実装前の要件
1. ヘルパー自体の単体テスト
2. 既存テストの回帰確認
3. 段階的移行計画

---

## P2-EX-04: 型安全性向上 ⭐⭐

### 目的
- バグ予防
- コードの可読性向上

### 提案内容

```python
from typing import TypeAlias

# Type aliases for clarity
SQLParam: TypeAlias = str | int | None
SQLParams: TypeAlias = list[SQLParam]

def _build_notes_filter(
    status: Optional[str],
    priority: Optional[str],
    comment: Optional[str],
) -> tuple[str, SQLParams]:
    """Build WHERE clause with type-safe params."""
    where: list[str] = []
    params: SQLParams = []  # 型安全
    
    # ...
    
    return where_sql, params
```

### メリット
- `list[object]` より具体的
- mypy/pyrightでの型チェック精度向上
- 意図が明確

### 実装前の要件
1. Python 3.10+ (TypeAlias使用のため)
2. mypy/pyright設定確認
3. 既存コードへの影響確認

---

## 📊 優先順位マトリクス

| 提案 | 価値 | リスク | 工数 | 優先度 |
|------|------|--------|------|--------|
| P2-EX-01: フィルタ分離 | ⭐⭐⭐ | 低 | 中 | **高** |
| P2-EX-02: PATCH構造化 | ⭐⭐ | **高** | 大 | 中 |
| P2-EX-03: ヘルパー共通化 | ⭐ | 低 | 小 | 低 |
| P2-EX-04: 型安全性 | ⭐⭐ | 低 | 小 | 中 |

---

## 🎯 実装ロードマップ

### Phase A: 安全な改善（リスク低）
1. **P2-EX-04: 型安全性向上**（工数: 小、リスク: 低）
2. **P2-EX-03: ヘルパー共通化**（工数: 小、リスク: 低）

### Phase B: 価値の高い改善
3. **P2-EX-01: フィルタ分離**（工数: 中、リスク: 低）
   - 単体テスト先行
   - 段階的リファクタ

### Phase C: 慎重に進める改善
4. **P2-EX-02: PATCH構造化**（工数: 大、リスク: 高）
   - 契約明文化
   - テスト拡充
   - UPDATE安全性検証

---

## ⚠️ 共通の注意事項

### 1. 「完全防止」などの断言は避ける

```python
# ❌ 言い過ぎ
"""SQL Injection 完全防止"""

# ✅ 正確
"""
SQL Injection 対策:
- WHERE句断片は定数文字列のみ
- プレースホルダー数は len() で決定
- 値はパラメータ化クエリで渡す
"""
```

### 2. 機能追加とリファクタを混ぜない

```python
# ❌ 混ぜている
def _build_notes_filter(...):
    # status複数指定対応（機能追加）
    statuses = status.split(",")
    # フィルタロジック分離（リファクタ）
```

**対応**: 
- 機能追加は別PR
- リファクタは単独PR

### 3. UPDATE での None 混入に注意

```python
# ❌ DANGEROUS
UPDATE notes SET status = ?, priority = ? WHERE id = ?
# 変更してない列に None が入る可能性

# ✅ SAFE
if status_changed:
    UPDATE notes SET status = ? WHERE id = ?
if priority_changed:
    UPDATE notes SET priority = ? WHERE id = ?
```

---

## 📝 まとめ

これらの提案は**「後でやる改善案としては上出来」**

ただし:
1. **P1.6では実装しない**（リファクタ禁止、最小差分）
2. **P2-EXで段階的に**（テスト先行、契約明確化）
3. **危険ポイントを把握**（UPDATE の None 混入など）

**取り込み方**:
- P1.6: 現行パッチで確定
- P2-EX-01: フィルタ分離（単独タスク）
- P2-EX-02: PATCH構造化（テスト＆契約先行）
- P2-EX-03/04: 安全な改善から順次

---

## 🔗 参考資料

- P1.6パッチ: `p1_6_filters_priority_comment_null.patch`
- 今やること: `P1_6_IMPLEMENTATION_NOW.md`
- 元のレビュー: `refactoring_analysis.md`
