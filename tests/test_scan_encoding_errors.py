# tests/test_scan_encoding_errors.py
from app import db


def test_scan_skips_non_utf8_files(client, tmp_path):
    """Contract: scan must skip non-UTF-8 files without crashing."""
    # Delete note if exists (self-contained, no import from other test files)
    slug = "valid_slug"
    con = db()
    try:
        con.execute("DELETE FROM notes WHERE slug = ?", (slug,))
        con.commit()
    finally:
        con.close()
    
    # Create a valid UTF-8 file with NOTE
    valid = tmp_path / "valid.py"
    valid.write_text("# NOTE(vNext): valid_slug\n", encoding="utf-8")
    
    # Create a non-UTF-8 file with .py extension (will be scanned but fail to decode)
    # Using Latin-1 bytes that are invalid UTF-8
    invalid = tmp_path / "invalid.py"
    invalid.write_bytes(b"# NOTE(vNext): \xff\xfe invalid data")
    
    # scan should succeed (skip invalid.py) and pick up valid_slug
    r = client.post("/scan?full=0", json={"root": str(tmp_path)})
    assert r.status_code == 200
    
    r2 = client.get("/export/notes")
    slugs = {n["slug"] for n in r2.json()["notes"]}
    assert "valid_slug" in slugs
