import requests
from datetime import datetime, timezone

BASE = "https://app.onepagecrm.com/api/v3"


def _auth(config):
    return (config["opcrm_user_id"], config["opcrm_api_key"])


def fetch_all_contacts(config):
    """Returns list of raw contact wrapper dicts: [{contact: {...}, next_actions: [...]}, ...]"""
    contacts = []
    page = 1
    while True:
        r = requests.get(
            f"{BASE}/contacts.json",
            auth=_auth(config),
            params={"page": page, "per_page": 100},
            timeout=30,
        )
        if r.status_code == 401:
            raise RuntimeError("OnePageCRM auth failed — check User ID and API Key in config.json.")
        r.raise_for_status()
        data = r.json().get("data", {})
        if isinstance(data, list):
            batch = data
        else:
            batch = data.get("contacts", [])
        if not batch:
            break
        contacts.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return contacts


def fetch_next_actions(config):
    r = requests.get(
        f"{BASE}/actions.json",
        auth=_auth(config),
        params={"per_page": 100},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json().get("data", {})
    return data.get("actions", []) if isinstance(data, dict) else data


def _fetch_all_pages(config, endpoint, key, extra_params=None):
    """Fetch all pages from a paginated endpoint, returning a flat list."""
    items = []
    page = 1
    params = {"per_page": 100, **(extra_params or {})}
    while True:
        params["page"] = page
        r = requests.get(f"{BASE}/{endpoint}", auth=_auth(config), params=params, timeout=30)
        r.raise_for_status()
        data = r.json().get("data", {})
        batch = data.get(key, []) if isinstance(data, dict) else data
        if not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def fetch_notes(config, contact_id):
    r = requests.get(
        f"{BASE}/notes.json",
        auth=_auth(config),
        params={"contact_id": contact_id, "per_page": 100},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json().get("data", {})
    return data.get("notes", []) if isinstance(data, dict) else data


def fetch_calls(config, contact_id):
    r = requests.get(
        f"{BASE}/calls.json",
        auth=_auth(config),
        params={"contact_id": contact_id, "per_page": 100},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json().get("data", {})
    return data.get("calls", []) if isinstance(data, dict) else data


def fetch_meetings(config, contact_id):
    r = requests.get(
        f"{BASE}/meetings.json",
        auth=_auth(config),
        params={"contact_id": contact_id, "per_page": 100},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json().get("data", {})
    return data.get("meetings", []) if isinstance(data, dict) else data


def fetch_all_notes(config):
    """Fetch all notes across all contacts (bulk, paginated)."""
    return _fetch_all_pages(config, "notes.json", "notes")


def fetch_all_calls(config):
    """Fetch all calls across all contacts (bulk, paginated)."""
    return _fetch_all_pages(config, "calls.json", "calls")


def fetch_all_meetings(config):
    """Fetch all meetings across all contacts (bulk, paginated)."""
    return _fetch_all_pages(config, "meetings.json", "meetings")


def fetch_all_deals(config):
    """Fetch all deals. Handles OnePageCRM's variable response shapes for this endpoint."""
    r = requests.get(
        f"{BASE}/deals.json",
        auth=_auth(config),
        params={"per_page": 100},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    # OnePageCRM returns deals in one of three shapes (mirroring Copilot dashboard logic)
    if isinstance(body.get("data"), list):
        raw_deals = body["data"]
    elif isinstance(body.get("data"), dict):
        raw_deals = body["data"].get("deals", [])
    else:
        raw_deals = body.get("deals", [])
    return [d.get("deal", d) for d in raw_deals]


def fetch_all_pipelines(config):
    """Fetch all pipelines including their stages. Handles variable response shapes."""
    r = requests.get(f"{BASE}/pipelines.json", auth=_auth(config), timeout=30)
    r.raise_for_status()
    body = r.json()
    if isinstance(body.get("data"), list):
        raw = body["data"]
    elif isinstance(body.get("data"), dict):
        raw = body["data"].get("pipelines", [])
    else:
        raw = body.get("pipelines", [])
    return [p.get("pipeline", p) for p in raw if isinstance(p, dict)]


def create_note(config, contact_id, text):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    r = requests.post(
        f"{BASE}/notes.json",
        auth=_auth(config),
        json={"note": {"contact_id": contact_id, "text": text, "date": today}},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def create_action(config, contact_id, text, due_date):
    r = requests.post(
        f"{BASE}/actions.json",
        auth=_auth(config),
        json={"action": {"contact_id": contact_id, "text": text, "date": due_date}},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def complete_action(config, action_id):
    r = requests.put(
        f"{BASE}/actions/{action_id}.json",
        auth=_auth(config),
        json={"action": {"status": "done"}},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def reschedule_action(config, action_id, new_date):
    r = requests.put(
        f"{BASE}/actions/{action_id}.json",
        auth=_auth(config),
        json={"action": {"date": new_date}},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def update_cadence(config, contact_id, months):
    field_id = "699669362480755968b7997e"
    r = requests.put(
        f"{BASE}/contacts/{contact_id}.json",
        auth=_auth(config),
        json={"contact": {"custom_fields": [{"id": field_id, "value": str(months)}]}},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def add_tag(config, contact_id, tag):
    r = requests.get(f"{BASE}/contacts/{contact_id}.json", auth=_auth(config), timeout=30)
    r.raise_for_status()
    contact = r.json().get("data", {}).get("contact", {})
    tags = list(contact.get("tags", []))
    if tag not in tags:
        tags.append(tag)
    r2 = requests.put(
        f"{BASE}/contacts/{contact_id}.json",
        auth=_auth(config),
        json={"contact": {"tags": tags}},
        timeout=30,
    )
    r2.raise_for_status()
    return r2.json()


def remove_tag(config, contact_id, tag):
    r = requests.get(f"{BASE}/contacts/{contact_id}.json", auth=_auth(config), timeout=30)
    r.raise_for_status()
    contact = r.json().get("data", {}).get("contact", {})
    tags = [t for t in contact.get("tags", []) if t != tag]
    r2 = requests.put(
        f"{BASE}/contacts/{contact_id}.json",
        auth=_auth(config),
        json={"contact": {"tags": tags}},
        timeout=30,
    )
    r2.raise_for_status()
    return r2.json()
