import requests
from datetime import datetime, timedelta, timezone

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
EMAIL_FIELDS = "id,subject,bodyPreview,receivedDateTime,from,toRecipients,isDraft,conversationId"


def fetch_emails(token, since_date=None, until_date=None, folder="me/messages"):
    """
    Fetch non-draft emails from the given folder.
    since_date / until_date: ISO 8601 strings e.g. '2026-01-01T00:00:00Z'.
    since_date defaults to 90 days ago if not provided.
    """
    if since_date is None:
        dt = datetime.now(timezone.utc) - timedelta(days=90)
        since_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    filter_parts = [f"receivedDateTime ge {since_date}", "isDraft eq false"]
    if until_date:
        filter_parts.insert(1, f"receivedDateTime lt {until_date}")
    filter_str = " and ".join(filter_parts)

    headers = {"Authorization": f"Bearer {token}"}
    params = {"$filter": filter_str, "$select": EMAIL_FIELDS, "$top": 100}
    url = f"{GRAPH_BASE}/{folder}"
    emails = []

    while url:
        r = requests.get(url, headers=headers, params=params if emails == [] else None)
        if r.status_code == 401:
            raise RuntimeError("Graph token expired — run sync again to re-authenticate.")
        r.raise_for_status()
        data = r.json()
        for msg in data.get("value", []):
            if not msg.get("isDraft"):
                emails.append(msg)
        url = data.get("@odata.nextLink")
        params = None  # nextLink already has params encoded

    return emails


def fetch_calendar_events(token, since_date=None, until_date=None):
    """
    Fetch calendar events in the given date range.
    Uses calendarView for reliable range queries.
    since_date defaults to 90 days ago; until_date defaults to 90 days ahead.
    """
    now = datetime.now(timezone.utc)
    if since_date is None:
        since_date = (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if until_date is None:
        until_date = (now + timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")

    headers = {"Authorization": f"Bearer {token}", "Prefer": 'outlook.timezone="UTC"'}
    params = {
        "startDateTime": since_date,
        "endDateTime": until_date,
        "$select": "id,subject,start,end,organizer,attendees,bodyPreview",
        "$top": 100,
    }
    url = f"{GRAPH_BASE}/me/calendarView"
    events = []

    while url:
        r = requests.get(url, headers=headers, params=params if not events else None)
        if r.status_code == 401:
            raise RuntimeError("Graph token expired — run sync again to re-authenticate.")
        r.raise_for_status()
        data = r.json()
        events.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        params = None

    return events
