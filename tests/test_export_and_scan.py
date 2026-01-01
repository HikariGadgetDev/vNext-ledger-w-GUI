# root/vnext-ledger/tests/test_export_and_scan.py

from pathlib import Path

from app import db


def _delete_note(slug: str) -> None:
    con = db()
    try:
        con.execute("DELETE FROM notes WHERE slug = ?", (slug,))
        con.commit()
    finally:
        con.close()


def _insert_note(slug: str, *, is_deleted: int = 0, is_archived: int = 0) -> None:
    con = db()
    try:
        # 既にあれば消してから入れ直す（再実行耐性）
        con.execute("DELETE FROM notes WHERE slug = ?", (slug,))

        # v4 schema 前提: created_at/updated_at は NOT NULL
        con.execute(
            """
            INSERT INTO notes (
              slug, first_seen, last_seen, evidence_count, status, priority,
              created_at, updated_at, is_deleted, is_archived
            )
            VALUES (?, '1970-01-01T00:00:00Z', '1970-01-01T00:00:00Z', 0, 'open', 3,
                    '1970-01-01T00:00:00Z', '1970-01-01T00:00:00Z', ?, ?)
            """,
            (slug, is_deleted, is_archived),
        )
        con.commit()
    finally:
        con.close()


def test_export_notes_excludes_deleted_and_archived_by_default(client):
    active = "exp_active"
    deleted = "exp_deleted"
    archived = "exp_archived"

    _insert_note(active, is_deleted=0, is_archived=0)
    _insert_note(deleted, is_deleted=1, is_archived=0)
    _insert_note(archived, is_deleted=0, is_archived=1)

    r = client.get("/export/notes")
    assert r.status_code == 200

    slugs = {n["slug"] for n in r.json()["notes"]}
    assert active in slugs
    assert deleted not in slugs
    assert archived not in slugs


def test_export_notes_can_include_deleted_and_archived(client):
    active = "exp2_active"
    deleted = "exp2_deleted"
    archived = "exp2_archived"

    _insert_note(active, is_deleted=0, is_archived=0)
    _insert_note(deleted, is_deleted=1, is_archived=0)
    _insert_note(archived, is_deleted=0, is_archived=1)

    r = client.get("/export/notes?include_deleted=1&include_archived=1")
    assert r.status_code == 200

    slugs = {n["slug"] for n in r.json()["notes"]}
    assert active in slugs
    assert deleted in slugs
    assert archived in slugs


def test_scan_adds_note_minimal_case(client, tmp_path: Path):
    slug = "scan_min"
    _delete_note(slug)

    # 最小の対象ファイル（拡張子は SCAN_EXTS に合わせて .py）
    p = tmp_path / "a.py"
    p.write_text(f"# NOTE(vNext): {slug}\n", encoding="utf-8")

    # local mode の想定: req.root が使われる
    r = client.post("/scan?full=0", json={"root": str(tmp_path)})
    assert r.status_code == 200

    # 反映確認: export に現れる
    r2 = client.get("/export/notes")
    slugs = {n["slug"] for n in r2.json()["notes"]}
    assert slug in slugs


def test_scan_picks_unicode_slug_and_exports(client, tmp_path):
    """Contract: Unicode slug (e.g. 日本語) must be scannable and exportable."""
    slug = "日本語テスト"
    _delete_note(slug)

    p = tmp_path / "a.py"
    p.write_text(f"# NOTE(vNext): {slug}\n", encoding="utf-8")

    r = client.post("/scan?full=0", json={"root": str(tmp_path)})
    assert r.status_code == 200

    r2 = client.get("/export/notes")
    slugs = {n["slug"] for n in r2.json()["notes"]}
    assert slug in slugs
