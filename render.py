# render.py
import html
import json
from pathlib import Path
from urllib.parse import quote


def esc(s: str | None) -> str:
    """
    HTML escape for any user/DB-derived text inserted into HTML strings.
    Always escape (including quotes).
    """
    return html.escape(s or "", quote=True)


def render_notes_table(notes: list[dict]) -> str:
    rows = []
    for n in notes:
        status = esc(n["status"])
        priority = n.get("priority")
        priority_txt = "-" if priority is None else esc(str(priority))
        # Contract (P1.5): slug may be Unicode; HTML uses esc() for display and quote(..., safe="") for href.
        # Ref: tests/test_slug_unicode_ui.py::test_notes_table_html_renders_unicode_slug_and_encoded_href
        slug_txt = esc(n["slug"])
        slug_url = quote(n["slug"], safe="")
        evidence_count = n["evidence_count"]

        status_class = {
            "open": "status-open",
            "doing": "status-doing",
            "parked": "status-parked",
            "done": "status-done",
            "stale": "status-stale",
        }.get(status, "")

        rows.append(
            f"""
            <tr>
                <td><span class="{status_class}">{status}</span></td>
                <td>{priority_txt}</td>
                <td><a href="/notes/{slug_url}">{slug_txt}</a></td>
                <td>{evidence_count}</td>
            </tr>
            """
        )

    if not rows:
        rows.append("<tr><td colspan='4'>No notes found</td></tr>")

    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>Notes</title></head>
    <body>
    <h1>Notes</h1>
    <table border="1">
        <thead>
            <tr>
                <th>Status</th>
                <th>Priority</th>
                <th>Slug</th>
                <th>Evidence</th>
            </tr>
        </thead>
        <tbody>
            {"".join(rows)}
        </tbody>
    </table>
    </body>
    </html>
    """


def render_note_detail(note: dict, evidence: list[dict], events: list[dict]) -> str:
    slug = esc(note["slug"])
    status = esc(note["status"])
    p = note.get("priority")
    priority = "-" if p is None else esc(str(p))
    created = esc(note["created_at"])
    updated = esc(note["updated_at"])

    evidence_rows = []
    for e in evidence:
        filepath = esc(e["filepath"])
        line_no = e["line_no"]
        snippet = esc(e["snippet"])
        created_at = esc(e["created_at"])
        evidence_rows.append(
            f"""
            <tr>
                <td>{filepath}</td>
                <td>{line_no}</td>
                <td>{snippet}</td>
                <td>{created_at}</td>
            </tr>
            """
        )

    if not evidence_rows:
        evidence_rows.append("<tr><td colspan='4'>No evidence</td></tr>")

    event_rows = []
    for ev in events:
        event_type = esc(ev["event_type"])
        old_value = esc(ev["old_value"]) if ev["old_value"] else "-"
        new_value = esc(ev["new_value"]) if ev["new_value"] else "-"
        changed_at = esc(ev["changed_at"])
        event_rows.append(
            f"""
            <tr>
                <td>{event_type}</td>
                <td>{old_value}</td>
                <td>{new_value}</td>
                <td>{changed_at}</td>
            </tr>
            """
        )

    if not event_rows:
        event_rows.append("<tr><td colspan='4'>No events</td></tr>")

    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>Note: {slug}</title></head>
    <body>
    <h1>Note: {slug}</h1>
    <p><strong>Status:</strong> {status}</p>
    <p><strong>Priority:</strong> {priority}</p>
    <p><strong>Created:</strong> {created}</p>
    <p><strong>Updated:</strong> {updated}</p>

    <h2>Evidence</h2>
    <table border="1">
        <thead>
            <tr>
                <th>Filepath</th>
                <th>Line</th>
                <th>Snippet</th>
                <th>Created At</th>
            </tr>
        </thead>
        <tbody>
            {"".join(evidence_rows)}
        </tbody>
    </table>

    <h2>Events</h2>
    <table border="1">
        <thead>
            <tr>
                <th>Event</th>
                <th>Old Value</th>
                <th>New Value</th>
                <th>Changed At</th>
            </tr>
        </thead>
        <tbody>
            {"".join(event_rows)}
        </tbody>
    </table>

    <p><a href="/notes/table">← Back to Notes</a></p>
    </body>
    </html>
    """


def render_summary(data: dict, allowed_statuses: list[str]) -> str:
    total = data["total"]
    by_status = data["by_status"]
    last_scan = esc(data["last_scan_at"]) if data["last_scan_at"] else "Never"

    status_rows = []
    for status in allowed_statuses:
        count = by_status.get(status, 0)
        status_rows.append(f"<tr><td>{status}</td><td>{count}</td></tr>")

    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>Summary</title></head>
    <body>
    <h1>Summary</h1>
    <p><strong>Total Notes:</strong> {total}</p>
    <p><strong>Last Scan:</strong> {last_scan}</p>

    <h2>By Status</h2>
    <table border="1">
        <thead>
            <tr><th>Status</th><th>Count</th></tr>
        </thead>
        <tbody>
            {"".join(status_rows)}
        </tbody>
    </table>
    </body>
    </html>
    """


def render_metrics(data: dict) -> str:
    exported_at = esc(data["exported_at"])
    last_scan = esc(data["last_scan_at"]) if data["last_scan_at"] else "Never"
    limit = data["limit"]

    agg = data["aggregate"]
    agg_all = data["aggregate_all"]

    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>Metrics</title></head>
    <body>
    <h1>Metrics</h1>
    <p><strong>Exported At:</strong> {exported_at}</p>
    <p><strong>Last Scan:</strong> {last_scan}</p>
    <p><strong>Limit:</strong> {limit}</p>

    <h2>Aggregate (recent {limit})</h2>
    <pre>{json.dumps(agg, indent=2)}</pre>

    <h2>Aggregate (all time)</h2>
    <pre>{json.dumps(agg_all, indent=2)}</pre>
    </body>
    </html>
    """


def render_scan_result(
    full: bool,
    root_path: Path,
    files_scanned: int,
    slugs_found: int,
    evidence_added: int,
    done_forced: int,
    stale_marked: int,
    revived_count: int,
    orphan_files_removed: int,
) -> str:
    scan_type = "Full Scan" if full else "Diff Scan"
    root = esc(str(root_path))

    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>Scan Result</title></head>
    <body>
    <h1>{scan_type} Complete</h1>
    <p><strong>Root:</strong> {root}</p>
    <p><strong>Files Scanned:</strong> {files_scanned}</p>
    <p><strong>Slugs Found:</strong> {slugs_found}</p>
    <p><strong>Evidence Added:</strong> {evidence_added}</p>
    <p><strong>Done Forced:</strong> {done_forced}</p>
    <p><strong>Stale Marked:</strong> {stale_marked}</p>
    <p><strong>Revived:</strong> {revived_count}</p>
    <p><strong>Orphan Files Removed:</strong> {orphan_files_removed}</p>
    </body>
    </html>
    """


def render_no_ui() -> str:
    """Fallback HTML when index.htmx not found"""
    return "<h1>vNext Ledger</h1><p>No UI found</p>"
