import requests
from datetime import datetime, timedelta, timezone

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
EMAIL_FIELDS = "id,subject,bodyPreview,receivedDateTime,from,toRecipients,isDraft,conversationId"


def fetch_emails(token, since_date=None, folder="me/messages"):
    """
    Fetch non-draft emails from the given folder.
    Returns list of raw Graph message dicts.
    since_date: ISO 8601 string e.g. '2026-01-01T00:00:00Z'. Defaults to 90 days ago.
    """
    if since_date is None:
        dt = datetime.now(timezone.utc) - timedelta(days=90)
        since_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    filter_str = f"receivedDateTime ge {since_date} and isDraft eq false"
    url = (
        f"{GRAPH_BASE}/{folder}"
        f"?$filter={filter_str}"
        f"&$select={EMAIL_FIELDS}"
        f"&$top=100"
    )
    headers = {"Authorization": f"Bearer {token}"}
    emails = []

    while url:
        r = requests.get(url, headers=headers)
        if r.status_code == 401:
            raise RuntimeError(
                "Graph token expired — run sync again to re-authenticate."
            )
        r.raise_for_status()
        data = r.json()
        for msg in data.get("value", []):
            if not msg.get("isDraft"):
                emails.append(msg)
        url = data.get("@odata.nextLink")

    return emails
