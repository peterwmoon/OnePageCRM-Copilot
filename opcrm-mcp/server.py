"""
OnePageCRM Copilot MCP Server

Run with: python server.py
Configure Claude Desktop to connect via stdio.
"""
from datetime import date, timedelta
from mcp.server.fastmcp import FastMCP

import time

import auth
import db
import linkedin as li
import sync as sync_module
import write_helpers

mcp = FastMCP("OnePageCRM Copilot")

# Load config and open persistent DB connection at startup
_config = auth.load_config()
db.init_db()
_conn = db.get_conn()

# In-progress Graph device flow (set by start_graph_auth, consumed by complete_graph_auth)
_pending_device_flow: dict = {}


# ── Sync tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def sync() -> dict:
    """
    Sync contacts, notes, calls, and meetings from OnePageCRM into the local cache.
    Does NOT sync Outlook emails — use start_graph_auth / complete_graph_auth first
    if Outlook has not been authorized yet, then call sync_emails().
    Returns a summary of what was synced.
    """
    return sync_module.sync_opcrm(_config, conn=_conn)


@mcp.tool()
def sync_history() -> dict:
    """
    Bulk-sync notes, calls, and meetings for all contacts from OnePageCRM.
    This is slower (many API calls) — run it once after first sync, then weekly.
    sync() must have been run first to populate contacts.
    """
    return sync_module.sync_history(_config, conn=_conn)


@mcp.tool()
def sync_emails() -> dict:
    """
    Incrementally sync Outlook emails since the last sync.
    Matched emails (sender/recipient in CRM) go to the emails table.
    Unmatched emails go to unmatched_emails for contact discovery.
    Requires Graph authorization — call start_graph_auth() first if needed.
    """
    return sync_module.sync_graph(_config, conn=_conn)


@mcp.tool()
def sync_calendar() -> dict:
    """
    Incrementally sync Outlook calendar events since the last sync.
    Requires Graph authorization — call start_graph_auth() first if needed.
    """
    return sync_module.sync_calendar(_config, conn=_conn)


@mcp.tool()
def find_unknown_contacts(min_emails: int = 2, limit: int = 0) -> list:
    """
    Return people who have emailed you but are not in your CRM.
    Grouped by sender address, sorted by frequency. Filters out automated senders.
    min_emails: minimum number of emails to be included (default 2).
    limit: max results (default 0 = no limit).
    Use this to discover contacts missing from OnePageCRM.
    """
    return db.get_unknown_contact_candidates(_conn, min_emails=min_emails, limit=limit or None)


@mcp.tool()
def detect_job_changes() -> dict:
    """
    Compare the two most recent LinkedIn connection snapshots to find people who changed jobs.
    Returns two lists:
      - changes: people matched to CRM contacts, with suggested outreach dates
      - not_in_crm: changed connections not found in your CRM
    Run linkedin_import.py first to populate snapshots. Requires at least two imports.
    """
    email_map = {
        row["email"].lower(): row["id"]
        for row in _conn.execute(
            "SELECT id, email FROM contacts WHERE email != ''"
        ).fetchall()
    }
    result = li.detect_job_changes(_conn, email_map)
    if result is None:
        return {
            "message": (
                "Only one LinkedIn snapshot exists — run linkedin_import.py again "
                "in ~3 months to detect changes."
            )
        }
    return result


@mcp.tool()
def create_job_change_action(
    contact_id: str,
    detected_date: str,
    new_company: str,
    confirmed: bool = False,
) -> dict:
    """
    Create a follow-up next action for a contact who changed jobs.
    The action is due 90 days after detected_date.
    Call with confirmed=False first to preview, then confirmed=True to execute.

    contact_id:    CRM contact ID (from detect_job_changes output).
    detected_date: YYYY-MM-DD date the job change was detected (from detect_job_changes).
    new_company:   The contact's new company name (from detect_job_changes).
    """
    contact = db.get_contact_by_id(_conn, contact_id)
    if contact is None:
        return {"error": f"Contact {contact_id} not found. Run sync first."}

    try:
        due_date = (
            date.fromisoformat(detected_date) + timedelta(days=90)
        ).isoformat()
    except ValueError:
        return {
            "error": f"Invalid detected_date '{detected_date}'. Use YYYY-MM-DD format."
        }

    text = f"Job change follow-up — {contact['name']} moved to {new_company}"
    return write_helpers.create_next_action(
        _config, _conn, contact_id, text, due_date, confirmed
    )


@mcp.tool()
def start_graph_auth() -> dict:
    """
    Begin Microsoft Graph (Outlook) OAuth authorization via device code flow.
    Returns a URL and a short code for the user to enter at that URL.
    After the user completes authorization in their browser, call complete_graph_auth().
    """
    import requests
    global _pending_device_flow
    r = requests.post(
        f"https://login.microsoftonline.com/{_config['graph_tenant_id']}/oauth2/v2.0/devicecode",
        data={
            "client_id": _config["graph_client_id"],
            "scope": "Mail.Read Calendars.Read User.Read offline_access",
        },
    )
    r.raise_for_status()
    flow = r.json()
    _pending_device_flow = {
        "device_code": flow["device_code"],
        "interval": flow.get("interval", 5),
        "expires_at": time.time() + flow.get("expires_in", 900),
    }
    return {
        "action": "Visit the URL below and enter the code to authorize Outlook access.",
        "url": flow["verification_uri"],
        "code": flow["user_code"],
        "next_step": "After completing authorization in your browser, call complete_graph_auth().",
    }


@mcp.tool()
def complete_graph_auth() -> dict:
    """
    Complete Microsoft Graph authorization after the user has entered the device code.
    Call this after start_graph_auth() once you've authorized in the browser.
    """
    import requests
    global _pending_device_flow
    if not _pending_device_flow:
        return {"error": "No authorization in progress. Call start_graph_auth() first."}
    if time.time() > _pending_device_flow["expires_at"]:
        _pending_device_flow = {}
        return {"error": "Authorization code expired. Call start_graph_auth() to restart."}

    poll = requests.post(
        f"https://login.microsoftonline.com/{_config['graph_tenant_id']}/oauth2/v2.0/token",
        data={
            "client_id": _config["graph_client_id"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": _pending_device_flow["device_code"],
        },
    )
    data = poll.json()
    if "access_token" in data:
        _config["graph_access_token"] = data["access_token"]
        _config["graph_refresh_token"] = data.get("refresh_token", "")
        _config["graph_token_expiry"] = time.time() + data.get("expires_in", 3600)
        auth.save_config(_config)
        _pending_device_flow = {}
        return {"status": "authorized", "message": "Outlook access granted. You can now call sync_emails()."}
    error = data.get("error", "")
    if error == "authorization_pending":
        return {"status": "pending", "message": "Not yet authorized. Complete the steps in your browser, then call complete_graph_auth() again."}
    _pending_device_flow = {}
    return {"error": f"Authorization failed: {data.get('error_description', error)}"}


@mcp.tool()
def sync_status() -> dict:
    """
    Show the last sync time and record counts for each data source.
    Does not make any API calls.
    """
    counts = {
        "contacts": _conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
        "notes": _conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0],
        "calls": _conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0],
        "meetings": _conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0],
        "emails": _conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0],
        "next_actions": _conn.execute("SELECT COUNT(*) FROM next_actions").fetchone()[0],
    }
    return {"record_counts": counts, "last_sync": db.get_sync_status(_conn)}


# ── Read tools — contacts ───────────────────────────────────────────────────────

@mcp.tool()
def get_contact(id_or_name: str) -> dict:
    """
    Get a contact's full profile: details, cadence, tags, owner, and open next action.
    Can search by contact ID or partial name match.
    """
    # Try by ID first, then by name search
    result = db.get_contact_by_id(_conn, id_or_name)
    if result is None:
        matches = db.search_contacts(_conn, id_or_name)
        if not matches:
            return {"error": f"No contact found matching '{id_or_name}'"}
        if len(matches) == 1:
            result = db.get_contact_by_id(_conn, matches[0]["id"])
        else:
            return {
                "multiple_matches": [
                    {"id": m["id"], "name": m["name"], "company": m["company"]}
                    for m in matches
                ],
                "message": "Multiple contacts matched. Use the specific contact ID."
            }
    action = _conn.execute(
        "SELECT * FROM next_actions WHERE contact_id = ? ORDER BY due_date LIMIT 1",
        (result["id"],)
    ).fetchone()
    result["next_action"] = dict(action) if action else None
    return result


@mcp.tool()
def search_contacts(query: str) -> list:
    """
    Search contacts by name, company, or email address.
    Returns a list of matches with basic profile info.
    """
    return db.search_contacts(_conn, query)


@mcp.tool()
def list_contacts_by_tag(tag: str) -> list:
    """
    Return all contacts with a given tag.
    Use get_all_tags() to see what tags exist.
    """
    return db.list_contacts_by_tag(_conn, tag)


@mcp.tool()
def get_all_tags() -> list:
    """List all tags used across contacts in the local cache."""
    return db.get_all_tags(_conn)


@mcp.tool()
def list_overdue_contacts() -> list:
    """
    Return contacts where the last touch (across notes, calls, meetings, and emails)
    exceeds their cadence window, or where there is no interaction history at all.
    Sorted by most overdue first.
    """
    return db.list_overdue_contacts(_conn)


@mcp.tool()
def list_contacts_by_owner(owner_id: str) -> list:
    """Return all contacts assigned to a specific owner ID."""
    return db.list_contacts_by_owner(_conn, owner_id)


# ── Read tools — history ───────────────────────────────────────────────────────

@mcp.tool()
def get_contact_history(contact_id: str, limit: int = 50) -> list:
    """
    Get a unified timeline of notes, calls, meetings, and emails for a contact,
    sorted by date descending. Use limit to control how many entries to return.
    """
    return db.get_contact_history(_conn, contact_id, limit)


@mcp.tool()
def get_recent_emails(contact_id: str, limit: int = 20) -> list:
    """Get the most recent emails for a contact, with subject and body preview."""
    return db.get_recent_emails(_conn, contact_id, limit)


@mcp.tool()
def get_notes(contact_id: str) -> list:
    """Get all OnePageCRM notes for a contact, sorted by date descending."""
    return db.get_notes(_conn, contact_id)


# ── Read tools — analysis ──────────────────────────────────────────────────────

@mcp.tool()
def summarize_relationship(contact_id: str) -> dict:
    """
    Summarize relationship health for a contact: last touch date, channel,
    cadence status, days overdue (if any), open next action, and recent topics
    extracted from note and email text.
    """
    contact = db.get_contact_by_id(_conn, contact_id)
    if contact is None:
        return {"error": f"Contact {contact_id} not found. Run sync first."}

    history = db.get_contact_history(_conn, contact_id, limit=10)
    last_touch = history[0] if history else None

    action = _conn.execute(
        "SELECT * FROM next_actions WHERE contact_id = ? ORDER BY due_date LIMIT 1",
        (contact_id,)
    ).fetchone()

    days_since = None
    if last_touch and last_touch.get("date"):
        try:
            last_date = date.fromisoformat(last_touch["date"][:10])
            days_since = (date.today() - last_date).days
        except ValueError:
            pass

    cadence_days = round(contact["cadence_months"] * 30.44)
    overdue_by = (days_since - cadence_days) if days_since is not None else None

    return {
        "contact": {"id": contact["id"], "name": contact["name"],
                    "company": contact["company"], "tags": contact["tags"]},
        "cadence_months": contact["cadence_months"],
        "last_touch": last_touch,
        "days_since_last_touch": days_since,
        "overdue_by_days": overdue_by if (overdue_by is not None and overdue_by > 0) else None,
        "open_next_action": dict(action) if action else None,
        "recent_topics": [
            {"type": h["type"], "date": h["date"],
             "excerpt": (h.get("content") or h.get("subject") or "")[:200]}
            for h in history[:5]
        ],
    }


@mcp.tool()
def cluster_contacts(by: str) -> dict:
    """
    Group contacts for batch outreach planning.
    by: 'tag' | 'company' | 'cadence_status'
    cadence_status groups into: overdue, upcoming (within 30 days), ok
    """
    if by == "tag":
        tags = db.get_all_tags(_conn)
        return {
            tag: [{"id": c["id"], "name": c["name"], "company": c["company"]}
                  for c in db.list_contacts_by_tag(_conn, tag)]
            for tag in tags
        }
    elif by == "company":
        rows = _conn.execute(
            "SELECT * FROM contacts WHERE company != '' ORDER BY company, name"
        ).fetchall()
        result = {}
        for row in rows:
            company = row["company"]
            result.setdefault(company, []).append(
                {"id": row["id"], "name": row["name"]}
            )
        return result
    elif by == "cadence_status":
        overdue = db.list_overdue_contacts(_conn)
        overdue_ids = {r["id"] for r in overdue}
        all_contacts = _conn.execute("SELECT * FROM contacts").fetchall()
        upcoming, ok = [], []
        for c in all_contacts:
            if c["id"] in overdue_ids:
                continue
            cadence_days = round(c["cadence_months"] * 30.44)
            row = _conn.execute("""
                SELECT MAX(date) AS last_date FROM (
                    SELECT date FROM notes WHERE contact_id = ?
                    UNION ALL SELECT date FROM calls WHERE contact_id = ?
                    UNION ALL SELECT date FROM emails WHERE contact_id = ?
                )
            """, (c["id"], c["id"], c["id"])).fetchone()
            last_date_str = row["last_date"] if row else None
            days_since = None
            if last_date_str:
                try:
                    days_since = (date.today() - date.fromisoformat(last_date_str[:10])).days
                except ValueError:
                    pass
            entry = {"id": c["id"], "name": c["name"],
                     "days_since": days_since}
            if days_since is not None and days_since > cadence_days * 0.8:
                upcoming.append(entry)
            else:
                ok.append(entry)
        return {
            "overdue": [{"id": r["id"], "name": r["name"],
                         "days_since": r["days_since"]} for r in overdue],
            "upcoming": upcoming,
            "ok": ok,
        }
    else:
        return {"error": "Invalid value for 'by'. Use 'tag', 'company', or 'cadence_status'."}


# ── Write tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def log_note(contact_id: str, text: str, confirmed: bool = False) -> dict:
    """
    Log a note against a contact in OnePageCRM.
    Call with confirmed=False first to see a preview.
    Call with confirmed=True to execute after reviewing the preview.
    """
    return write_helpers.log_note(_config, _conn, contact_id, text, confirmed)


@mcp.tool()
def create_next_action(contact_id: str, text: str, due_date: str,
                       confirmed: bool = False) -> dict:
    """
    Create a next action for a contact. due_date format: YYYY-MM-DD.
    Call with confirmed=False first to see a preview (required for unowned contacts).
    Call with confirmed=True to execute.
    """
    return write_helpers.create_next_action(
        _config, _conn, contact_id, text, due_date, confirmed
    )


@mcp.tool()
def complete_next_action(action_id: str, confirmed: bool = False) -> dict:
    """
    Mark a next action as complete. Use sync_status() to verify action IDs.
    Call with confirmed=False first to preview, then confirmed=True to execute.
    """
    return write_helpers.complete_next_action(_config, _conn, action_id, confirmed)


@mcp.tool()
def reschedule_next_action(action_id: str, new_date: str,
                           confirmed: bool = False) -> dict:
    """
    Reschedule a next action to a new date (YYYY-MM-DD format).
    Call with confirmed=False to preview, confirmed=True to execute.
    """
    return write_helpers.reschedule_next_action(
        _config, _conn, action_id, new_date, confirmed
    )


@mcp.tool()
def update_cadence(contact_id: str, months: int, confirmed: bool = False) -> dict:
    """
    Update the cadence field for a contact (number of months between touches).
    Only works for contacts you own. Always previews first regardless of confirmed.
    This affects overdue detection in the CRM Co-Pilot browser app.
    """
    return write_helpers.update_cadence(_config, _conn, contact_id, months, confirmed)


@mcp.tool()
def add_tag(contact_id: str, tag: str, confirmed: bool = False) -> dict:
    """
    Add a tag to a contact. Blocked for contacts you do not own.
    Call with confirmed=False to preview, confirmed=True to execute.
    """
    return write_helpers.add_tag(_config, _conn, contact_id, tag, confirmed)


@mcp.tool()
def remove_tag(contact_id: str, tag: str, confirmed: bool = False) -> dict:
    """
    Remove a tag from a contact. Blocked for contacts you do not own.
    Call with confirmed=False to preview, confirmed=True to execute.
    """
    return write_helpers.remove_tag(_config, _conn, contact_id, tag, confirmed)


if __name__ == "__main__":
    mcp.run()  # stdio transport — Claude connects via MCP config
