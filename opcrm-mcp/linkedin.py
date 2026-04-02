"""
LinkedIn data processing: CSV parsing and job-change detection.
"""
import csv
import io
from datetime import date, timedelta


def parse_connections_csv(text):
    """
    Parse the content of LinkedIn's Connections.csv export.

    LinkedIn prepends a variable number of preamble lines before the actual
    CSV header. This function scans for the header row (which always starts
    with 'First Name') and ignores everything before it.

    Returns a list of dicts with keys:
        linkedin_url, first_name, last_name, email,
        company, position, connected_on
    Rows without a linkedin_url are silently skipped.
    Raises ValueError if no header row is found.
    """
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        first_field = line.split(",")[0].strip().strip("\ufeff")
        if first_field == "First Name":
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(
            "Could not find header row in LinkedIn connections CSV. "
            "Expected a row starting with 'First Name'."
        )

    csv_text = "\n".join(lines[header_idx:]).lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(csv_text))

    connections = []
    for row in reader:
        url = row.get("URL", "").strip()
        if not url:
            continue
        connections.append({
            "linkedin_url": url,
            "first_name": row.get("First Name", "").strip(),
            "last_name": row.get("Last Name", "").strip(),
            "email": row.get("Email Address", "").strip().lower(),
            "company": row.get("Company", "").strip(),
            "position": row.get("Position", "").strip(),
            "connected_on": row.get("Connected On", "").strip(),
        })
    return connections


def detect_job_changes(conn, email_map):
    """
    Compare the two most recent LinkedIn snapshots and return job-change results.

    email_map: dict of {email_lower: contact_id} — used to match connections to CRM.

    Returns None if fewer than two snapshots exist.
    Returns dict:
        {
            "changes": [...],      # changed connections matched to CRM contacts
            "not_in_crm": [...],   # changed connections with no CRM match
        }
    Each item contains: name, linkedin_url, email, old_company, old_position,
    new_company, new_position, detected_date, suggested_outreach_date,
    and contact_id (changes list only).
    """
    dates = conn.execute("""
        SELECT DISTINCT snapshot_date FROM linkedin_connections
        ORDER BY snapshot_date DESC LIMIT 2
    """).fetchall()

    if len(dates) < 2:
        return None

    latest_date = dates[0]["snapshot_date"]
    prev_date = dates[1]["snapshot_date"]

    latest = {
        row["linkedin_url"]: dict(row)
        for row in conn.execute(
            "SELECT * FROM linkedin_connections WHERE snapshot_date = ?",
            (latest_date,),
        ).fetchall()
    }
    previous = {
        row["linkedin_url"]: dict(row)
        for row in conn.execute(
            "SELECT * FROM linkedin_connections WHERE snapshot_date = ?",
            (prev_date,),
        ).fetchall()
    }

    outreach_date = (
        date.fromisoformat(latest_date) + timedelta(days=90)
    ).isoformat()

    changes = []
    not_in_crm = []

    for url, current in latest.items():
        if url not in previous:
            continue  # new connection, not a job change
        prev = previous[url]
        if (current["company"] == prev["company"]
                and current["position"] == prev["position"]):
            continue  # no change

        entry = {
            "name": f"{current['first_name']} {current['last_name']}".strip(),
            "linkedin_url": url,
            "email": current.get("email", ""),
            "old_company": prev["company"],
            "old_position": prev["position"],
            "new_company": current["company"],
            "new_position": current["position"],
            "detected_date": latest_date,
            "suggested_outreach_date": outreach_date,
        }

        email = current.get("email", "")
        contact_id = email_map.get(email) if email else None

        if contact_id:
            entry["contact_id"] = contact_id
            changes.append(entry)
        else:
            not_in_crm.append(entry)

    return {"changes": changes, "not_in_crm": not_in_crm}
