# tests/test_slug_unicode_ui.py
import html
from urllib.parse import quote

from app import db


def test_notes_table_html_renders_unicode_slug_and_encoded_href(client, tmp_path):
    """Contract: /notes/table HTML must display Unicode slug and encode href properly."""
    slug = "日本語/パス"
    
    # Setup: delete note if exists (self-contained)
    con = db()
    try:
        con.execute("DELETE FROM notes WHERE slug = ?", (slug,))
        con.commit()
    finally:
        con.close()
    
    p = tmp_path / "unicode.py"
    p.write_text(f"# NOTE(vNext): {slug}\n", encoding="utf-8")
    client.post("/scan?full=0", json={"root": str(tmp_path)})
    
    # Act: fetch HTML table
    r = client.get("/notes/table")
    assert r.status_code == 200
    html_text = r.text
    
    # Assert: slug is displayed (HTML-escaped for safety)
    # Use html.escape to match what esc() does in render_notes_table
    expected_display = html.escape(slug, quote=True)
    assert expected_display in html_text, f"Unicode slug (HTML-escaped) must be visible: {expected_display}"
    
    # Assert: href is URL-encoded (safe="")
    expected_href = f"/notes/{quote(slug, safe='')}"
    assert expected_href in html_text, f"href must be URL-encoded: {expected_href}"
