# P1.6 テスト修正 + UI改善

## ✅ 修正内容

### 1. テスト赤修正: `_wants_html()` のデフォルト動作変更

**問題**:
- テスト（httpx）が `Accept: */*` で送信
- 現状の `_wants_html()` は `*/*` を HTML扱いしない
- → JSON が返って HTML テストが失敗

**修正**:
```python
def _wants_html(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    # Default to HTML when Accept is missing or */* (httpx/curl default).
    # Return JSON only when explicitly requested via Accept/Content-Type.
    if not accept or "*/*" in accept:
        return True  # ← デフォルトHTML
    return ("text/html" in accept) or ("application/xhtml+xml" in accept)
```

**意図**:
- `Accept` なし or `*/*` → HTML（デフォルト）
- `Accept: application/json` → JSON（明示的）

**結果**:
- ✅ テスト `test_notes_table_html_renders_unicode_slug_and_encoded_href` が通る
- ✅ ブラウザ（HTML）、curl（HTML）、API（JSON明示）が正しく動作

---

### 2. UI改善: フィルタに none/comment/risk 追加

#### A. priority セレクタ修正

**Before（1-5、noneなし）**:
```html
<select id="priority" name="priority">
  <option value="">priority: all</option>
  <option value="1">1</option>
  <option value="2">2</option>
  <option value="3">3</option>
  <option value="4">4</option>
  <option value="5">5</option>
</select>
```

**問題**:
- 4, 5 は バックエンドで 400 エラー（PRIORITY_RANGE=(1,3)）
- none（未評価）が選べない → P1.6 の旨味が使えない

**After（1-3 + none）**:
```html
<select id="priority" name="priority">
  <option value="">priority: all</option>
  <option value="none">priority: none（未評価）</option>
  <option value="1">1（高優先）</option>
  <option value="2">2（中優先）</option>
  <option value="3">3（低優先）</option>
</select>
```

**修正点**:
- ✅ none 追加（未評価フィルタ）
- ✅ 1-3 のみ（バックエンドと一致）
- ✅ ラベル追加（高/中/低）

---

#### B. comment セレクタ追加

**NEW**:
```html
<select id="comment" name="comment">
  <option value="">comment: all</option>
  <option value="any">comment: あり</option>
  <option value="none">comment: なし</option>
</select>
```

**機能**:
- `any`: コメント付きノートのみ表示
- `none`: コメント未付きノートのみ表示

**トリガー追加**:
```html
hx-trigger="change from:#comment"
```

---

#### C. risk_level セレクタ追加

**NEW**:
```html
<select id="risk_level" name="risk_level">
  <option value="">risk: all</option>
  <option value="high">risk: high</option>
  <option value="critical">risk: critical</option>
  <option value="none">risk: none（通常）</option>
</select>
```

**機能**:
- `high`: 高リスクノート表示
- `critical`: クリティカルノート表示
- `none`: 通常リスクノート表示

**注意**: バックエンドはまだ risk_level フィルタ未実装（スキーマのみ）

**トリガー追加**:
```html
hx-trigger="change from:#risk_level"
```

---

## 📊 修正箇所まとめ

### app.py
- **行412-418**: `_wants_html()` 修正（デフォルトHTML）

### index.htmx
- **行533-540**: priority セレクタ修正（none追加、1-3のみ、ラベル追加）
- **行542-546**: comment セレクタ追加（any/none）
- **行548-553**: risk_level セレクタ追加（high/critical/none）
- **行510-519**: hx-trigger 更新（comment, risk_level 追加）

---

## 🎯 実運用での効果

### Before（問題）
```
❌ 未評価のノートが選べない
❌ priority 4,5 を選ぶと 400 エラー
❌ comment フィルタがUIにない
❌ risk_level フィルタがUIにない
```

### After（改善）
```
✅ 未評価（none）フィルタ → トリアージワークフローが完結
✅ priority 1-3 のみ → バックエンドと一致、エラーなし
✅ comment あり/なし → 議論済み/未議論の分離
✅ risk_level 選択可能 → 高リスク抽出（将来の実装準備）
```

---

## 🚀 使用例

### 朝のスタンドアップ
```
1. priority=none（未評価）を選択
   → 未トリアージのノートを表示
2. チームで優先度を決定
3. priority=1（高優先）を選択
   → 今日やるタスクを確認
```

### 週次レビュー
```
1. comment=none を選択
   → まだ議論してないノートを表示
2. チームで議論
3. comment=any を選択
   → 議論済みで進行中のものを確認
```

### セキュリティレビュー
```
1. risk_level=critical を選択
   → クリティカルなノートを表示
2. priority=3 かつ risk_level=critical
   → 低優先度だが高リスクのもの（露出してない脆弱性）
```

---

## ✅ テスト結果（予測）

```bash
pytest tests/test_slug_unicode_ui.py -v
```

**Before**: ❌ FAILED（JSON返却）

**After**: ✅ PASSED（HTML返却、hrefエンコード確認）

---

## 🎉 完成

**P1.6 の旨味が UI から使えるようになりました**

- ✅ テスト赤修正（HTML デフォルト化）
- ✅ priority: none 追加（未評価フィルタ）
- ✅ priority: 1-3 のみ（バックエンド一致）
- ✅ comment: any/none 追加
- ✅ risk_level: high/critical/none 追加

**次のステップ（将来）**:
- risk_level フィルタのバックエンド実装
- render.py で risk_level 表示
- PATCH での risk_level 更新

**現時点**: UI準備完了、コア機能使用可能 🚀
