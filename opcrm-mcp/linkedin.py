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

    csv_text = "\n".join(lines[header_idx:])
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
