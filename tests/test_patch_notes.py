# root/vnext-ledger/tests/test_patch_notes.py

def test_patch_no_change_returns_204(client):
    r = client.patch("/notes/test", json={})
    assert r.status_code == 204

def test_patch_slug_with_trailing_slash(client):
    r = client.patch("/notes/test/", json={"comment": "x"}, follow_redirects=True)
    assert r.status_code == 200

def test_patch_status_uses_id_fallback(client):
    r = client.patch("/notes/test", json={"status": "doing"})
    assert r.status_code == 200
    assert r.json()["note"]["status"] == "doing"
