# tests/test_p1_ledger_pollution_contract.py
from __future__ import annotations

import sqlite3
from pathlib import Path


def _get_note(con: sqlite3.Connection, slug: str):
    return con.execute(
        "SELECT id, status, priority, updated_at FROM notes WHERE slug = ?",
        (slug,),
    ).fetchone()


def test_p1_noop_patch_returns_204_and_no_pollution(client, test_db: Path, temp_repo: Path):
    slug = "noop_slug"

    # NOTE を作る → DBに note を生やす
    p = temp_repo / "a.py"
    p.write_text(f"# NOTE(vNext): {slug}\n", encoding="utf-8")
    client.post("/scan?full=1", json={"root": str(temp_repo)})

    con = sqlite3.connect(test_db)
    try:
        row = _get_note(con, slug)
        assert row is not None
        note_id, old_status, old_priority, old_updated_at = row

        events_before = con.execute("SELECT COUNT(*) FROM note_events WHERE note_id = ?", (note_id,)).fetchone()[0]
    finally:
        con.close()

    # no-op PATCH（同値を入れる）
    r = client.patch(f"/notes/{slug}", json={"status": old_status, "priority": old_priority})
    assert r.status_code == 204, "Contract violation: no-op PATCH must return 204"

    con = sqlite3.connect(test_db)
    try:
        row2 = _get_note(con, slug)
        assert row2 is not None
        _, _, _, updated_after = row2

        events_after = con.execute("SELECT COUNT(*) FROM note_events WHERE note_id = ?", (note_id,)).fetchone()[0]
    finally:
        con.close()

    assert updated_after == old_updated_at, "Contract violation: no-op PATCH must not change updated_at"
    assert events_after == events_before, "Contract violation: no-op PATCH must not create events"


def test_p1_real_patch_returns_200(client, temp_repo: Path):
    slug = "real_patch_slug"
    p = temp_repo / "b.py"
    p.write_text(f"# NOTE(vNext): {slug}\n", encoding="utf-8")
    client.post("/scan?full=1", json={"root": str(temp_repo)})

    r = client.patch(f"/notes/{slug}", json={"status": "doing"})
    assert r.status_code == 200
    assert r.json()["status"] == "doing"
