# tests/test_table_filters.py
from __future__ import annotations

from app import db


def _delete_note(slug: str) -> None:
    con = db()
    try:
        con.execute("DELETE FROM notes WHERE slug = ?", (slug,))
        con.commit()
    finally:
        con.close()


def test_notes_table_filters_priority_and_comment(client, tmp_path):
    a = "flt_a"
    b = "flt_b"
    _delete_note(a)
    _delete_note(b)

    (tmp_path / "a.py").write_text(f"# NOTE(vNext): {a}\n", encoding="utf-8")
    (tmp_path / "b.py").write_text(f"# NOTE(vNext): {b}\n", encoding="utf-8")

    r = client.post("/scan?full=0", json={"root": str(tmp_path)})
    assert r.status_code == 200

    # New notes default to priority=None
    r = client.get(f"/notes/{a}", headers={"accept": "application/json"})
    assert r.status_code == 200
    assert r.json()["note"]["priority"] is None

    # Add a comment to a
    r = client.patch(f"/notes/{a}", json={"comment": "hello"})
    assert r.status_code == 200

    # Set priority for b
    r = client.patch(f"/notes/{b}", json={"priority": 1})
    assert r.status_code == 200

    # comment=any -> must include a, exclude b
    r = client.get("/notes/table?comment=any", headers={"accept": "application/json"})
    assert r.status_code == 200
    slugs = [n["slug"] for n in r.json()["notes"]]
    assert a in slugs and b not in slugs

    # comment=none -> must include b, exclude a
    r = client.get("/notes/table?comment=none", headers={"accept": "application/json"})
    assert r.status_code == 200
    slugs = [n["slug"] for n in r.json()["notes"]]
    assert b in slugs and a not in slugs

    # priority=1 -> must include b, exclude a
    r = client.get("/notes/table?priority=1", headers={"accept": "application/json"})
    assert r.status_code == 200
    slugs = [n["slug"] for n in r.json()["notes"]]
    assert b in slugs and a not in slugs

    # priority=none -> must include a, exclude b
    r = client.get("/notes/table?priority=none", headers={"accept": "application/json"})
    assert r.status_code == 200
    slugs = [n["slug"] for n in r.json()["notes"]]
    assert a in slugs and b not in slugs

    # combined -> must include a, exclude b
    r = client.get("/notes/table?comment=any&priority=none", headers={"accept": "application/json"})
    assert r.status_code == 200
    slugs = [n["slug"] for n in r.json()["notes"]]
    assert a in slugs and b not in slugs
