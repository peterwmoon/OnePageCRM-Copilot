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
        )
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
    )
    r.raise_for_status()
    data = r.json().get("data", {})
    return data.get("actions", []) if isinstance(data, dict) else data


def fetch_notes(config, contact_id):
    r = requests.get(
        f"{BASE}/notes.json",
        auth=_auth(config),
        params={"contact_id": contact_id, "per_page": 100},
    )
    r.raise_for_status()
    data = r.json().get("data", {})
    return data.get("notes", []) if isinstance(data, dict) else data


def fetch_calls(config, contact_id):
    r = requests.get(
        f"{BASE}/calls.json",
        auth=_auth(config),
        params={"contact_id": contact_id, "per_page": 100},
    )
    r.raise_for_status()
    data = r.json().get("data", {})
    return data.get("calls", []) if isinstance(data, dict) else data


def fetch_meetings(config, contact_id):
    r = requests.get(
        f"{BASE}/meetings.json",
        auth=_auth(config),
        params={"contact_id": contact_id, "per_page": 100},
    )
    r.raise_for_status()
    data = r.json().get("data", {})
    return data.get("meetings", []) if isinstance(data, dict) else data


def create_note(config, contact_id, text):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    r = requests.post(
        f"{BASE}/notes.json",
        auth=_auth(config),
        json={"note": {"contact_id": contact_id, "text": text, "date": today}},
    )
    r.raise_for_status()
    return r.json()


def create_action(config, contact_id, text, due_date):
    r = requests.post(
        f"{BASE}/actions.json",
        auth=_auth(config),
        json={"action": {"contact_id": contact_id, "text": text, "date": due_date}},
    )
    r.raise_for_status()
    return r.json()


def complete_action(config, action_id):
    r = requests.put(
        f"{BASE}/actions/{action_id}.json",
        auth=_auth(config),
        json={"action": {"status": "done"}},
    )
    r.raise_for_status()
    return r.json()


def reschedule_action(config, action_id, new_date):
    r = requests.put(
        f"{BASE}/actions/{action_id}.json",
        auth=_auth(config),
        json={"action": {"date": new_date}},
    )
    r.raise_for_status()
    return r.json()


def update_cadence(config, contact_id, months):
    field_id = "699669362480755968b7997e"
    r = requests.put(
        f"{BASE}/contacts/{contact_id}.json",
        auth=_auth(config),
        json={"contact": {"custom_fields": [{"id": field_id, "value": str(months)}]}},
    )
    r.raise_for_status()
    return r.json()


def add_tag(config, contact_id, tag):
    r = requests.get(f"{BASE}/contacts/{contact_id}.json", auth=_auth(config))
    r.raise_for_status()
    contact = r.json().get("data", {}).get("contact", {})
    tags = list(contact.get("tags", []))
    if tag not in tags:
        tags.append(tag)
    r2 = requests.put(
        f"{BASE}/contacts/{contact_id}.json",
        auth=_auth(config),
        json={"contact": {"tags": tags}},
    )
    r2.raise_for_status()
    return r2.json()


def remove_tag(config, contact_id, tag):
    r = requests.get(f"{BASE}/contacts/{contact_id}.json", auth=_auth(config))
    r.raise_for_status()
    contact = r.json().get("data", {}).get("contact", {})
    tags = [t for t in contact.get("tags", []) if t != tag]
    r2 = requests.put(
        f"{BASE}/contacts/{contact_id}.json",
        auth=_auth(config),
        json={"contact": {"tags": tags}},
    )
    r2.raise_for_status()
    return r2.json()
