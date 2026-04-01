# OnePageCRM MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Python MCP server that syncs OnePageCRM contacts/history and Outlook emails into SQLite, exposing read and write tools so Claude can analyze relationship health and propose or log outreach.

**Architecture:** Single Python package (`opcrm-mcp/`) with a FastMCP stdio server. SQLite is the source of truth for all Claude queries; OPCRM and Microsoft Graph APIs are only touched during sync. Write tools use a two-phase confirmed=false/true pattern to force explicit confirmation before any change is made.

**Tech Stack:** Python 3.11+, `mcp[cli]` (FastMCP), `requests`, `sqlite3` (stdlib), `pytest`

---

## Prerequisites (manual, before Task 1)

**Azure App Registration for device-code flow:**

The existing browser app (`index.html`) uses a public client OAuth PKCE flow. The MCP server needs the same tenant but uses device-code flow instead. You may reuse the existing Azure app registration **only if** "Allow public client flows" is already enabled. To verify/configure:

1. Open Azure Portal → Azure Active Directory → App Registrations → find your existing app
2. Under **Authentication**, confirm "Allow public client flows" is set to **Yes**
3. Under **API Permissions**, confirm `Mail.Read` and `User.Read` (delegated) are granted
4. Note the **Application (client) ID** and **Directory (tenant) ID** — you'll need these for `config.json`

If you need a new registration: create a new app, set platform to "Mobile and desktop", enable public client flows, add the same permissions.

---

## File Map

| File | Responsibility |
|---|---|
| `opcrm-mcp/db.py` | SQLite schema init + all upsert/query helpers |
| `opcrm-mcp/auth.py` | config.json R/W, Graph OAuth device-code flow + token refresh |
| `opcrm-mcp/opcrm.py` | OnePageCRM v3 HTTP client (read + write) |
| `opcrm-mcp/graph.py` | Microsoft Graph HTTP client (read emails) |
| `opcrm-mcp/sync.py` | Orchestrates OPCRM + Graph → SQLite sync |
| `opcrm-mcp/server.py` | FastMCP server; all `@mcp.tool()` definitions (thin wrappers over db.py/opcrm.py) |
| `opcrm-mcp/requirements.txt` | Python dependencies |
| `opcrm-mcp/.gitignore` | Ignores config.json + crm_cache.db |
| `opcrm-mcp/config.json.template` | Credential template (safe to commit) |
| `opcrm-mcp/tests/__init__.py` | Empty |
| `opcrm-mcp/tests/test_db.py` | DB schema + upsert + query tests (uses `:memory:`) |
| `opcrm-mcp/tests/test_auth.py` | Config load/save + token refresh tests (mocked HTTP) |
| `opcrm-mcp/tests/test_opcrm.py` | OPCRM API client tests (mocked HTTP) |
| `opcrm-mcp/tests/test_graph.py` | Graph client tests (mocked HTTP) |
| `opcrm-mcp/tests/test_sync.py` | Sync orchestration tests (mocked HTTP + `:memory:`) |
| `opcrm-mcp/tests/test_server.py` | Write tool ownership + two-phase confirmation tests |

---

## Task 1: Scaffold

**Files:**
- Create: `opcrm-mcp/requirements.txt`
- Create: `opcrm-mcp/.gitignore`
- Create: `opcrm-mcp/config.json.template`
- Create: `opcrm-mcp/tests/__init__.py`

- [ ] **Step 1: Create the directory structure**

```bash
mkdir -p H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp/tests
```

- [ ] **Step 2: Write requirements.txt**

`opcrm-mcp/requirements.txt`:
```
mcp[cli]>=1.0.0
requests>=2.31.0
pytest>=7.4.0
```

- [ ] **Step 3: Write .gitignore**

`opcrm-mcp/.gitignore`:
```
config.json
crm_cache.db
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 4: Write config.json.template**

`opcrm-mcp/config.json.template`:
```json
{
  "opcrm_user_id": "YOUR_ONEPAGECRM_USER_ID",
  "opcrm_api_key": "YOUR_ONEPAGECRM_API_KEY",
  "opcrm_my_user_id": "YOUR_ONEPAGECRM_USER_ID",
  "graph_client_id": "YOUR_AZURE_APP_CLIENT_ID",
  "graph_tenant_id": "YOUR_AZURE_TENANT_ID",
  "graph_access_token": "",
  "graph_refresh_token": "",
  "graph_token_expiry": 0
}
```

- [ ] **Step 5: Create empty tests/__init__.py**

`opcrm-mcp/tests/__init__.py`: (empty file)

- [ ] **Step 6: Install dependencies**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
pip install -r requirements.txt
```

Expected: packages install without error. Verify with `python -c "import mcp; import requests; print('OK')"`.

- [ ] **Step 7: Create config.json from template**

```bash
cp config.json.template config.json
```

Then fill in your real values in `config.json`. Leave `graph_access_token`, `graph_refresh_token`, `graph_token_expiry` blank — auth.py will populate them during the device-code flow.

- [ ] **Step 8: Commit scaffold**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot
git add opcrm-mcp/requirements.txt opcrm-mcp/.gitignore opcrm-mcp/config.json.template opcrm-mcp/tests/__init__.py
git commit -m "feat: scaffold opcrm-mcp Python package"
```

---

## Task 2: Database — Schema and Upsert Helpers

**Files:**
- Create: `opcrm-mcp/db.py`
- Create: `opcrm-mcp/tests/test_db.py`

- [ ] **Step 1: Write failing tests for schema init and basic upserts**

`opcrm-mcp/tests/test_db.py`:
```python
import sqlite3
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    return conn


def test_init_creates_all_tables():
    conn = make_conn()
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert tables == {
        "contacts", "contact_tags", "next_actions",
        "notes", "calls", "meetings", "emails", "sync_log"
    }


def test_upsert_contact():
    conn = make_conn()
    db.upsert_contact(conn, {
        "id": "c1", "name": "Alice Smith", "company": "Acme",
        "email": "alice@acme.com", "phone": "555-1234",
        "owner_id": "u1", "status": "active",
        "cadence_months": 3, "raw_json": "{}"
    })
    conn.commit()
    row = conn.execute("SELECT * FROM contacts WHERE id = 'c1'").fetchone()
    assert row["name"] == "Alice Smith"
    assert row["cadence_months"] == 3


def test_upsert_contact_is_idempotent():
    conn = make_conn()
    for _ in range(2):
        db.upsert_contact(conn, {
            "id": "c1", "name": "Alice Smith", "company": "Acme",
            "email": "alice@acme.com", "phone": "",
            "owner_id": "u1", "status": "active",
            "cadence_months": 6, "raw_json": "{}"
        })
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    assert count == 1


def test_upsert_tags_replaces_existing():
    conn = make_conn()
    db.upsert_contact(conn, {
        "id": "c1", "name": "Alice", "company": "", "email": "",
        "phone": "", "owner_id": "u1", "status": "active",
        "cadence_months": 6, "raw_json": "{}"
    })
    db.upsert_tags(conn, "c1", ["investor", "board"])
    db.upsert_tags(conn, "c1", ["board"])  # replaces
    conn.commit()
    tags = {row[0] for row in conn.execute(
        "SELECT tag FROM contact_tags WHERE contact_id = 'c1'"
    ).fetchall()}
    assert tags == {"board"}


def test_upsert_note():
    conn = make_conn()
    db.upsert_contact(conn, {
        "id": "c1", "name": "Alice", "company": "", "email": "",
        "phone": "", "owner_id": "u1", "status": "active",
        "cadence_months": 6, "raw_json": "{}"
    })
    db.upsert_note(conn, {
        "id": "n1", "contact_id": "c1",
        "text": "Met at conference", "date": "2026-01-15", "author_id": "u1"
    })
    conn.commit()
    row = conn.execute("SELECT * FROM notes WHERE id = 'n1'").fetchone()
    assert row["text"] == "Met at conference"


def test_log_sync():
    conn = make_conn()
    db.log_sync(conn, "opcrm", contacts_synced=10, records_synced=50)
    conn.commit()
    row = conn.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["source"] == "opcrm"
    assert row["contacts_synced"] == 10
    assert row["error"] is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
python -m pytest tests/test_db.py -v
```

Expected: `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: Write db.py**

`opcrm-mcp/db.py`:
```python
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "crm_cache.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY,
    name TEXT,
    company TEXT,
    email TEXT,
    phone TEXT,
    owner_id TEXT,
    status TEXT,
    cadence_months INTEGER DEFAULT 6,
    last_synced TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS contact_tags (
    contact_id TEXT,
    tag TEXT,
    PRIMARY KEY (contact_id, tag)
);

CREATE TABLE IF NOT EXISTS next_actions (
    id TEXT PRIMARY KEY,
    contact_id TEXT,
    text TEXT,
    due_date TEXT,
    assignee_id TEXT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    contact_id TEXT,
    text TEXT,
    date TEXT,
    author_id TEXT
);

CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    contact_id TEXT,
    text TEXT,
    date TEXT,
    author_id TEXT,
    duration INTEGER
);

CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    contact_id TEXT,
    text TEXT,
    date TEXT,
    author_id TEXT
);

CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY,
    contact_id TEXT,
    subject TEXT,
    body_preview TEXT,
    date TEXT,
    direction TEXT,
    thread_id TEXT,
    from_address TEXT,
    to_addresses TEXT
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    last_synced_at TEXT DEFAULT (datetime('now')),
    contacts_synced INTEGER DEFAULT 0,
    records_synced INTEGER DEFAULT 0,
    error TEXT
);
"""


def get_conn(db_path=None):
    """Return a connection. Pass ':memory:' for tests."""
    path = db_path if db_path is not None else str(DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn_or_path=None):
    """Create schema. Accepts a connection (for tests) or a path string."""
    if isinstance(conn_or_path, sqlite3.Connection):
        conn_or_path.executescript(SCHEMA)
        return
    conn = get_conn(conn_or_path)
    conn.executescript(SCHEMA)
    conn.close()


def upsert_contact(conn, c):
    conn.execute("""
        INSERT INTO contacts (id, name, company, email, phone, owner_id, status,
                              cadence_months, last_synced, raw_json)
        VALUES (:id, :name, :company, :email, :phone, :owner_id, :status,
                :cadence_months, datetime('now'), :raw_json)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name, company=excluded.company, email=excluded.email,
            phone=excluded.phone, owner_id=excluded.owner_id, status=excluded.status,
            cadence_months=excluded.cadence_months, last_synced=excluded.last_synced,
            raw_json=excluded.raw_json
    """, c)


def upsert_tags(conn, contact_id, tags):
    conn.execute("DELETE FROM contact_tags WHERE contact_id = ?", (contact_id,))
    for tag in tags:
        conn.execute(
            "INSERT OR IGNORE INTO contact_tags (contact_id, tag) VALUES (?, ?)",
            (contact_id, tag)
        )


def upsert_note(conn, n):
    conn.execute("""
        INSERT INTO notes (id, contact_id, text, date, author_id)
        VALUES (:id, :contact_id, :text, :date, :author_id)
        ON CONFLICT(id) DO UPDATE SET
            text=excluded.text, date=excluded.date
    """, n)


def upsert_call(conn, c):
    conn.execute("""
        INSERT INTO calls (id, contact_id, text, date, author_id, duration)
        VALUES (:id, :contact_id, :text, :date, :author_id, :duration)
        ON CONFLICT(id) DO UPDATE SET
            text=excluded.text, date=excluded.date
    """, c)


def upsert_meeting(conn, m):
    conn.execute("""
        INSERT INTO meetings (id, contact_id, text, date, author_id)
        VALUES (:id, :contact_id, :text, :date, :author_id)
        ON CONFLICT(id) DO UPDATE SET
            text=excluded.text, date=excluded.date
    """, m)


def upsert_action(conn, a):
    conn.execute("""
        INSERT INTO next_actions (id, contact_id, text, due_date, assignee_id, status)
        VALUES (:id, :contact_id, :text, :due_date, :assignee_id, :status)
        ON CONFLICT(id) DO UPDATE SET
            text=excluded.text, due_date=excluded.due_date,
            assignee_id=excluded.assignee_id, status=excluded.status
    """, a)


def upsert_email(conn, e):
    conn.execute("""
        INSERT INTO emails (id, contact_id, subject, body_preview, date,
                            direction, thread_id, from_address, to_addresses)
        VALUES (:id, :contact_id, :subject, :body_preview, :date,
                :direction, :thread_id, :from_address, :to_addresses)
        ON CONFLICT(id) DO UPDATE SET
            subject=excluded.subject, body_preview=excluded.body_preview,
            date=excluded.date, direction=excluded.direction
    """, e)


def log_sync(conn, source, contacts_synced=0, records_synced=0, error=None):
    conn.execute("""
        INSERT INTO sync_log (source, contacts_synced, records_synced, error)
        VALUES (?, ?, ?, ?)
    """, (source, contacts_synced, records_synced, error))
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
python -m pytest tests/test_db.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot
git add opcrm-mcp/db.py opcrm-mcp/tests/test_db.py
git commit -m "feat: database schema and upsert helpers"
```

---

## Task 3: Auth — Config I/O and Graph Device-Code Flow

**Files:**
- Create: `opcrm-mcp/auth.py`
- Create: `opcrm-mcp/tests/test_auth.py`

- [ ] **Step 1: Write failing tests**

`opcrm-mcp/tests/test_auth.py`:
```python
import json
import time
import sys
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import auth


def make_config(tmp_path):
    config = {
        "opcrm_user_id": "uid",
        "opcrm_api_key": "key",
        "opcrm_my_user_id": "uid",
        "graph_client_id": "client123",
        "graph_tenant_id": "tenant456",
        "graph_access_token": "",
        "graph_refresh_token": "",
        "graph_token_expiry": 0
    }
    p = Path(tmp_path) / "config.json"
    p.write_text(json.dumps(config))
    return config, str(p)


def test_load_config(tmp_path):
    config, path = make_config(tmp_path)
    loaded = auth.load_config(path)
    assert loaded["opcrm_user_id"] == "uid"
    assert loaded["graph_client_id"] == "client123"


def test_save_config(tmp_path):
    config, path = make_config(tmp_path)
    config["graph_access_token"] = "tok"
    auth.save_config(config, path)
    reloaded = auth.load_config(path)
    assert reloaded["graph_access_token"] == "tok"


def test_get_graph_token_returns_valid_cached_token(tmp_path):
    config, path = make_config(tmp_path)
    config["graph_access_token"] = "cached_token"
    config["graph_token_expiry"] = time.time() + 3600
    token = auth.get_graph_token(config, path)
    assert token == "cached_token"


def test_get_graph_token_refreshes_expired_token(tmp_path):
    config, path = make_config(tmp_path)
    config["graph_access_token"] = "old_token"
    config["graph_refresh_token"] = "refresh_tok"
    config["graph_token_expiry"] = time.time() - 1  # expired

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "access_token": "new_token",
        "refresh_token": "new_refresh",
        "expires_in": 3600
    }
    mock_response.raise_for_status = MagicMock()

    with patch("requests.post", return_value=mock_response) as mock_post:
        token = auth.get_graph_token(config, path)

    assert token == "new_token"
    mock_post.assert_called_once()
    call_data = mock_post.call_args[1]["data"]
    assert call_data["grant_type"] == "refresh_token"
    assert call_data["refresh_token"] == "refresh_tok"

    saved = auth.load_config(path)
    assert saved["graph_access_token"] == "new_token"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
python -m pytest tests/test_auth.py -v
```

Expected: `ModuleNotFoundError: No module named 'auth'`

- [ ] **Step 3: Write auth.py**

`opcrm-mcp/auth.py`:
```python
import json
import time
import requests
from pathlib import Path

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config(config_path=None):
    path = Path(config_path or _DEFAULT_CONFIG_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"config.json not found at {path}. "
            "Copy config.json.template, rename it config.json, and fill in your credentials."
        )
    with open(path) as f:
        return json.load(f)


def save_config(config, config_path=None):
    path = Path(config_path or _DEFAULT_CONFIG_PATH)
    with open(path, "w") as f:
        json.dump(config, f, indent=2)


def get_graph_token(config, config_path=None):
    """Return a valid Graph access token. Refreshes or runs device flow as needed."""
    if (config.get("graph_access_token")
            and config.get("graph_token_expiry", 0) > time.time() + 60):
        return config["graph_access_token"]

    if config.get("graph_refresh_token"):
        return _refresh_token(config, config_path)

    return _run_device_flow(config, config_path)


def _refresh_token(config, config_path=None):
    r = requests.post(
        f"https://login.microsoftonline.com/{config['graph_tenant_id']}/oauth2/v2.0/token",
        data={
            "client_id": config["graph_client_id"],
            "grant_type": "refresh_token",
            "refresh_token": config["graph_refresh_token"],
            "scope": "Mail.Read User.Read offline_access",
        },
    )
    r.raise_for_status()
    data = r.json()
    config["graph_access_token"] = data["access_token"]
    config["graph_refresh_token"] = data.get("refresh_token", config["graph_refresh_token"])
    config["graph_token_expiry"] = time.time() + data.get("expires_in", 3600)
    save_config(config, config_path)
    return config["graph_access_token"]


def _run_device_flow(config, config_path=None):
    r = requests.post(
        f"https://login.microsoftonline.com/{config['graph_tenant_id']}/oauth2/v2.0/devicecode",
        data={
            "client_id": config["graph_client_id"],
            "scope": "Mail.Read User.Read offline_access",
        },
    )
    r.raise_for_status()
    flow = r.json()

    print(f"\nTo authorize Outlook access, visit: {flow['verification_uri']}")
    print(f"Enter this code: {flow['user_code']}\n")

    interval = flow.get("interval", 5)
    deadline = time.time() + flow.get("expires_in", 900)

    while time.time() < deadline:
        time.sleep(interval)
        poll = requests.post(
            f"https://login.microsoftonline.com/{config['graph_tenant_id']}/oauth2/v2.0/token",
            data={
                "client_id": config["graph_client_id"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": flow["device_code"],
            },
        )
        data = poll.json()
        if "access_token" in data:
            config["graph_access_token"] = data["access_token"]
            config["graph_refresh_token"] = data.get("refresh_token", "")
            config["graph_token_expiry"] = time.time() + data.get("expires_in", 3600)
            save_config(config, config_path)
            print("Outlook authentication successful.")
            return config["graph_access_token"]
        if data.get("error") == "slow_down":
            interval += 5
        elif data.get("error") != "authorization_pending":
            raise RuntimeError(
                f"Device flow failed: {data.get('error_description', data.get('error'))}"
            )

    raise RuntimeError("Device flow timed out. Re-run sync to try again.")
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
python -m pytest tests/test_auth.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot
git add opcrm-mcp/auth.py opcrm-mcp/tests/test_auth.py
git commit -m "feat: config I/O and Graph OAuth device-code flow"
```

---

## Task 4: OnePageCRM API Client

**Files:**
- Create: `opcrm-mcp/opcrm.py`
- Create: `opcrm-mcp/tests/test_opcrm.py`

**Important:** No CORS proxy needed here — Python processes are not browser clients.

- [ ] **Step 1: Write failing tests**

`opcrm-mcp/tests/test_opcrm.py`:
```python
import sys
import os
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import opcrm

CONFIG = {"opcrm_user_id": "uid", "opcrm_api_key": "key"}


def mock_response(data):
    m = MagicMock()
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


def test_fetch_all_contacts_single_page():
    contacts_payload = [
        {"contact": {"id": "c1", "first_name": "Alice", "last_name": "Smith",
                     "emails": [{"value": "alice@acme.com"}], "company_name": "Acme",
                     "tags": ["investor"], "custom_fields": [], "owner_id": "u1",
                     "status": "active"}},
    ]
    with patch("requests.get", return_value=mock_response(
        {"data": {"contacts": contacts_payload}}
    )):
        result = opcrm.fetch_all_contacts(CONFIG)
    assert len(result) == 1
    assert result[0]["contact"]["id"] == "c1"


def test_fetch_notes_for_contact():
    notes_payload = [{"id": "n1", "contact_id": "c1", "text": "Called", "date": "2026-01-10", "author_id": "u1"}]
    with patch("requests.get", return_value=mock_response(
        {"data": {"notes": notes_payload}}
    )):
        result = opcrm.fetch_notes(CONFIG, "c1")
    assert len(result) == 1
    assert result[0]["id"] == "n1"


def test_create_note_posts_correct_payload():
    with patch("requests.post", return_value=mock_response({"data": {"note": {"id": "n2"}}})) as mock_post:
        opcrm.create_note(CONFIG, "c1", "Follow up on proposal")
    call_json = mock_post.call_args[1]["json"]
    assert call_json["note"]["contact_id"] == "c1"
    assert call_json["note"]["text"] == "Follow up on proposal"


def test_add_tag_appends_to_existing():
    existing = {"contact": {"id": "c1", "tags": ["investor"]}}
    with patch("requests.get", return_value=mock_response({"data": existing})):
        with patch("requests.put", return_value=mock_response({"data": {}})) as mock_put:
            opcrm.add_tag(CONFIG, "c1", "board")
    tags_sent = mock_put.call_args[1]["json"]["contact"]["tags"]
    assert set(tags_sent) == {"investor", "board"}


def test_remove_tag_strips_tag():
    existing = {"contact": {"id": "c1", "tags": ["investor", "board"]}}
    with patch("requests.get", return_value=mock_response({"data": existing})):
        with patch("requests.put", return_value=mock_response({"data": {}})) as mock_put:
            opcrm.remove_tag(CONFIG, "c1", "investor")
    tags_sent = mock_put.call_args[1]["json"]["contact"]["tags"]
    assert tags_sent == ["board"]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
python -m pytest tests/test_opcrm.py -v
```

Expected: `ModuleNotFoundError: No module named 'opcrm'`

- [ ] **Step 3: Write opcrm.py**

`opcrm-mcp/opcrm.py`:
```python
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
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
python -m pytest tests/test_opcrm.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot
git add opcrm-mcp/opcrm.py opcrm-mcp/tests/test_opcrm.py
git commit -m "feat: OnePageCRM v3 API client"
```

---

## Task 5: Microsoft Graph Client

**Files:**
- Create: `opcrm-mcp/graph.py`
- Create: `opcrm-mcp/tests/test_graph.py`

- [ ] **Step 1: Write failing tests**

`opcrm-mcp/tests/test_graph.py`:
```python
import sys
import os
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import graph


def mock_response(data, status=200):
    m = MagicMock()
    m.json.return_value = data
    m.status_code = status
    m.raise_for_status = MagicMock()
    if status == 401:
        m.raise_for_status.side_effect = Exception("401")
    return m


def test_fetch_emails_single_page():
    payload = {
        "value": [
            {
                "id": "msg1",
                "subject": "Proposal follow-up",
                "bodyPreview": "Hi Peter, following up...",
                "receivedDateTime": "2026-03-01T10:00:00Z",
                "from": {"emailAddress": {"address": "alice@acme.com"}},
                "toRecipients": [{"emailAddress": {"address": "pmoon@navicet.com"}}],
                "isDraft": False,
                "threadId": "thread1",
            }
        ]
    }
    with patch("requests.get", return_value=mock_response(payload)):
        emails = graph.fetch_emails("tok", folder="me/messages")
    assert len(emails) == 1
    assert emails[0]["id"] == "msg1"


def test_fetch_emails_skips_drafts():
    payload = {
        "value": [
            {"id": "d1", "isDraft": True, "subject": "Draft", "bodyPreview": "",
             "receivedDateTime": "2026-03-01T10:00:00Z",
             "from": {"emailAddress": {"address": "x@y.com"}},
             "toRecipients": [], "threadId": "t1"},
        ]
    }
    with patch("requests.get", return_value=mock_response(payload)):
        emails = graph.fetch_emails("tok")
    assert len(emails) == 0


def test_fetch_emails_raises_on_401():
    with patch("requests.get", return_value=mock_response({}, status=401)):
        try:
            graph.fetch_emails("expired_token")
            assert False, "Should have raised"
        except RuntimeError as e:
            assert "expired" in str(e).lower()


def test_fetch_emails_follows_next_link():
    page1 = {
        "value": [{"id": "m1", "isDraft": False, "subject": "S1", "bodyPreview": "",
                   "receivedDateTime": "2026-03-01T10:00:00Z",
                   "from": {"emailAddress": {"address": "a@b.com"}},
                   "toRecipients": [], "threadId": "t1"}],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skip=100"
    }
    page2 = {
        "value": [{"id": "m2", "isDraft": False, "subject": "S2", "bodyPreview": "",
                   "receivedDateTime": "2026-03-01T09:00:00Z",
                   "from": {"emailAddress": {"address": "a@b.com"}},
                   "toRecipients": [], "threadId": "t2"}]
    }
    responses = [mock_response(page1), mock_response(page2)]
    with patch("requests.get", side_effect=responses):
        emails = graph.fetch_emails("tok")
    assert len(emails) == 2
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
python -m pytest tests/test_graph.py -v
```

Expected: `ModuleNotFoundError: No module named 'graph'`

- [ ] **Step 3: Write graph.py**

`opcrm-mcp/graph.py`:
```python
import requests
from datetime import datetime, timedelta, timezone

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
EMAIL_FIELDS = "id,subject,bodyPreview,receivedDateTime,from,toRecipients,isDraft,threadId"


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
        f"&$orderby=receivedDateTime desc"
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
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
python -m pytest tests/test_graph.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot
git add opcrm-mcp/graph.py opcrm-mcp/tests/test_graph.py
git commit -m "feat: Microsoft Graph email client"
```

---

## Task 6: Sync Orchestrator

**Files:**
- Create: `opcrm-mcp/sync.py`
- Create: `opcrm-mcp/tests/test_sync.py`

- [ ] **Step 1: Write failing tests**

`opcrm-mcp/tests/test_sync.py`:
```python
import json
import sqlite3
import sys
import os
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db
import sync

CONFIG = {
    "opcrm_user_id": "uid", "opcrm_api_key": "key",
    "opcrm_my_user_id": "uid",
    "graph_client_id": "c", "graph_tenant_id": "t",
    "graph_access_token": "tok", "graph_refresh_token": "ref",
    "graph_token_expiry": 9999999999
}

RAW_CONTACT = {
    "contact": {
        "id": "c1", "first_name": "Alice", "last_name": "Smith",
        "emails": [{"value": "alice@acme.com"}], "company_name": "Acme Corp",
        "tags": ["investor"], "owner_id": "uid", "status": "active",
        "custom_fields": [
            {"custom_field": {"id": "699669362480755968b7997e"}, "value": "3"}
        ]
    }
}


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    return conn


def test_sync_opcrm_inserts_contact():
    conn = make_db()
    with patch("opcrm.fetch_all_contacts", return_value=[RAW_CONTACT]), \
         patch("opcrm.fetch_next_actions", return_value=[]), \
         patch("opcrm.fetch_notes", return_value=[]), \
         patch("opcrm.fetch_calls", return_value=[]), \
         patch("opcrm.fetch_meetings", return_value=[]):
        result = sync.sync_opcrm(CONFIG, conn=conn)

    conn.commit()
    row = conn.execute("SELECT * FROM contacts WHERE id = 'c1'").fetchone()
    assert row["name"] == "Alice Smith"
    assert row["cadence_months"] == 3
    assert row["email"] == "alice@acme.com"
    assert result["contacts_synced"] == 1


def test_sync_opcrm_inserts_tags():
    conn = make_db()
    with patch("opcrm.fetch_all_contacts", return_value=[RAW_CONTACT]), \
         patch("opcrm.fetch_next_actions", return_value=[]), \
         patch("opcrm.fetch_notes", return_value=[]), \
         patch("opcrm.fetch_calls", return_value=[]), \
         patch("opcrm.fetch_meetings", return_value=[]):
        sync.sync_opcrm(CONFIG, conn=conn)

    conn.commit()
    tags = {row[0] for row in conn.execute(
        "SELECT tag FROM contact_tags WHERE contact_id = 'c1'"
    ).fetchall()}
    assert tags == {"investor"}


def test_sync_graph_matches_email_to_contact():
    conn = make_db()
    db.upsert_contact(conn, {
        "id": "c1", "name": "Alice Smith", "company": "Acme",
        "email": "alice@acme.com", "phone": "", "owner_id": "uid",
        "status": "active", "cadence_months": 6, "raw_json": "{}"
    })
    conn.commit()

    email_msg = {
        "id": "msg1", "subject": "Checking in", "bodyPreview": "Hi Peter",
        "receivedDateTime": "2026-03-01T10:00:00Z", "isDraft": False,
        "threadId": "t1",
        "from": {"emailAddress": {"address": "alice@acme.com"}},
        "toRecipients": [{"emailAddress": {"address": "pmoon@navicet.com"}}],
    }

    with patch("graph.fetch_emails", return_value=[email_msg]), \
         patch("auth.get_graph_token", return_value="tok"):
        result = sync.sync_graph(CONFIG, conn=conn)

    conn.commit()
    row = conn.execute("SELECT * FROM emails WHERE id = 'msg1'").fetchone()
    assert row is not None
    assert row["contact_id"] == "c1"
    assert row["direction"] == "in"
    assert result["emails_synced"] == 1


def test_sync_graph_discards_unmatched_email():
    conn = make_db()
    conn.commit()

    email_msg = {
        "id": "msg_unknown", "subject": "Unknown", "bodyPreview": "...",
        "receivedDateTime": "2026-03-01T10:00:00Z", "isDraft": False,
        "threadId": "t1",
        "from": {"emailAddress": {"address": "stranger@nowhere.com"}},
        "toRecipients": [{"emailAddress": {"address": "pmoon@navicet.com"}}],
    }

    with patch("graph.fetch_emails", return_value=[email_msg]), \
         patch("auth.get_graph_token", return_value="tok"):
        sync.sync_graph(CONFIG, conn=conn)

    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    assert count == 0
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
python -m pytest tests/test_sync.py -v
```

Expected: `ModuleNotFoundError: No module named 'sync'`

- [ ] **Step 3: Write sync.py**

`opcrm-mcp/sync.py`:
```python
import json
import sqlite3
from datetime import datetime, timezone

import auth
import db
import graph
import opcrm

CADENCE_FIELD_ID = "699669362480755968b7997e"
DEFAULT_CADENCE_MONTHS = 6


def _parse_contact(raw):
    """Extract structured fields from a raw OPCRM contact wrapper."""
    c = raw.get("contact", raw)

    cadence_months = DEFAULT_CADENCE_MONTHS
    for field in c.get("custom_fields", []):
        nested_id = field.get("custom_field", {}).get("id", "")
        if nested_id == CADENCE_FIELD_ID and field.get("value"):
            try:
                cadence_months = int(field["value"])
            except (ValueError, TypeError):
                pass

    emails = c.get("emails", [])
    primary_email = emails[0].get("value", "").strip() if emails else ""

    phones = c.get("phones", [])
    primary_phone = phones[0].get("value", "").strip() if phones else ""

    return {
        "id": c["id"],
        "name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
        "company": c.get("company_name", ""),
        "email": primary_email,
        "phone": primary_phone,
        "owner_id": c.get("owner_id", ""),
        "status": c.get("status", ""),
        "cadence_months": cadence_months,
        "raw_json": json.dumps(c),
    }


def _parse_note(raw, contact_id):
    n = raw.get("note", raw)
    return {
        "id": n["id"],
        "contact_id": contact_id,
        "text": n.get("text", ""),
        "date": n.get("date", ""),
        "author_id": n.get("author_id", n.get("user_id", "")),
    }


def _parse_call(raw, contact_id):
    c = raw.get("call", raw)
    return {
        "id": c["id"],
        "contact_id": contact_id,
        "text": c.get("text", ""),
        "date": c.get("date", ""),
        "author_id": c.get("author_id", c.get("user_id", "")),
        "duration": c.get("duration", 0) or 0,
    }


def _parse_meeting(raw, contact_id):
    m = raw.get("meeting", raw)
    return {
        "id": m["id"],
        "contact_id": contact_id,
        "text": m.get("text", ""),
        "date": m.get("date", ""),
        "author_id": m.get("author_id", m.get("user_id", "")),
    }


def _parse_action(raw):
    a = raw.get("action", raw)
    return {
        "id": a["id"],
        "contact_id": a.get("contact_id", ""),
        "text": a.get("text", ""),
        "due_date": a.get("date", ""),
        "assignee_id": a.get("assignee_id", a.get("user_id", "")),
        "status": a.get("status", ""),
    }


def sync_opcrm(config, conn=None):
    """Sync all OPCRM data into the database. Pass conn for testing."""
    owns_conn = conn is None
    if owns_conn:
        conn = db.get_conn()
        db.init_db(conn)

    try:
        raw_contacts = opcrm.fetch_all_contacts(config)
        records = 0

        for raw in raw_contacts:
            contact = _parse_contact(raw)
            db.upsert_contact(conn, contact)

            c = raw.get("contact", raw)
            db.upsert_tags(conn, contact["id"], c.get("tags", []))

            for raw_note in opcrm.fetch_notes(config, contact["id"]):
                db.upsert_note(conn, _parse_note(raw_note, contact["id"]))
                records += 1

            for raw_call in opcrm.fetch_calls(config, contact["id"]):
                db.upsert_call(conn, _parse_call(raw_call, contact["id"]))
                records += 1

            for raw_meeting in opcrm.fetch_meetings(config, contact["id"]):
                db.upsert_meeting(conn, _parse_meeting(raw_meeting, contact["id"]))
                records += 1

        for raw_action in opcrm.fetch_next_actions(config):
            db.upsert_action(conn, _parse_action(raw_action))
            records += 1

        db.log_sync(conn, "opcrm", len(raw_contacts), records)
        conn.commit()
        return {"contacts_synced": len(raw_contacts), "records_synced": records, "error": None}

    except Exception as e:
        db.log_sync(conn, "opcrm", 0, 0, str(e))
        conn.commit()
        raise
    finally:
        if owns_conn:
            conn.close()


def sync_graph(config, conn=None):
    """Sync Outlook emails matched to OPCRM contacts. Pass conn for testing."""
    token = auth.get_graph_token(config)

    owns_conn = conn is None
    if owns_conn:
        conn = db.get_conn()

    try:
        rows = conn.execute("SELECT id, email FROM contacts WHERE email != ''").fetchall()
        email_map = {row["email"].lower(): row["id"] for row in rows}

        all_emails = []
        for folder in ["me/messages", "me/mailFolders/sentItems/messages"]:
            all_emails.extend(graph.fetch_emails(token, folder=folder))

        inserted = 0
        for msg in all_emails:
            from_addr = (
                msg.get("from", {}).get("emailAddress", {}).get("address", "").lower()
            )
            to_addrs = [
                r["emailAddress"]["address"].lower()
                for r in msg.get("toRecipients", [])
            ]

            contact_id = email_map.get(from_addr)
            direction = "in"
            if not contact_id:
                for addr in to_addrs:
                    if addr in email_map:
                        contact_id = email_map[addr]
                        direction = "out"
                        break

            if not contact_id:
                continue

            db.upsert_email(conn, {
                "id": msg["id"],
                "contact_id": contact_id,
                "subject": msg.get("subject", ""),
                "body_preview": msg.get("bodyPreview", ""),
                "date": msg.get("receivedDateTime", ""),
                "direction": direction,
                "thread_id": msg.get("threadId", ""),
                "from_address": from_addr,
                "to_addresses": json.dumps(to_addrs),
            })
            inserted += 1

        db.log_sync(conn, "graph", 0, inserted)
        conn.commit()
        return {"emails_synced": inserted, "error": None}

    except Exception as e:
        db.log_sync(conn, "graph", 0, 0, str(e))
        conn.commit()
        raise
    finally:
        if owns_conn:
            conn.close()


def full_sync(config):
    """Run OPCRM sync then Graph sync. Returns combined summary."""
    result_opcrm = sync_opcrm(config)
    result_graph = sync_graph(config)
    return {**result_opcrm, **result_graph}
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
python -m pytest tests/test_sync.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot
git add opcrm-mcp/sync.py opcrm-mcp/tests/test_sync.py
git commit -m "feat: OPCRM and Graph sync orchestrator"
```

---

## Task 7: Read Query Functions

**Files:**
- Modify: `opcrm-mcp/db.py` (add query functions)
- Modify: `opcrm-mcp/tests/test_db.py` (add query tests)

- [ ] **Step 1: Add failing tests for query functions**

Append to `opcrm-mcp/tests/test_db.py`:
```python
import json
from datetime import date, timedelta


def seed_contact(conn, contact_id="c1", name="Alice Smith", email="alice@acme.com",
                 owner_id="u1", cadence_months=3, tags=None):
    db.upsert_contact(conn, {
        "id": contact_id, "name": name, "company": "Acme", "email": email,
        "phone": "", "owner_id": owner_id, "status": "active",
        "cadence_months": cadence_months, "raw_json": json.dumps({"id": contact_id})
    })
    db.upsert_tags(conn, contact_id, tags or [])


def test_get_contact_by_id():
    conn = make_conn()
    seed_contact(conn)
    conn.commit()
    result = db.get_contact_by_id(conn, "c1")
    assert result is not None
    assert result["name"] == "Alice Smith"
    assert result["tags"] == []


def test_get_contact_includes_tags():
    conn = make_conn()
    seed_contact(conn, tags=["investor", "board"])
    conn.commit()
    result = db.get_contact_by_id(conn, "c1")
    assert set(result["tags"]) == {"investor", "board"}


def test_search_contacts_by_name():
    conn = make_conn()
    seed_contact(conn, contact_id="c1", name="Alice Smith")
    seed_contact(conn, contact_id="c2", name="Bob Jones", email="bob@jones.com")
    conn.commit()
    results = db.search_contacts(conn, "alice")
    assert len(results) == 1
    assert results[0]["id"] == "c1"


def test_list_contacts_by_tag():
    conn = make_conn()
    seed_contact(conn, contact_id="c1", tags=["investor"])
    seed_contact(conn, contact_id="c2", email="b@b.com", tags=["board"])
    conn.commit()
    results = db.list_contacts_by_tag(conn, "investor")
    assert len(results) == 1
    assert results[0]["id"] == "c1"


def test_list_overdue_contacts():
    conn = make_conn()
    # Contact with cadence of 3 months, last touched 4 months ago
    seed_contact(conn, contact_id="c1", cadence_months=3)
    old_date = (date.today() - timedelta(days=130)).isoformat()
    db.upsert_note(conn, {
        "id": "n1", "contact_id": "c1", "text": "Old note",
        "date": old_date, "author_id": "u1"
    })
    # Contact with no history at all
    seed_contact(conn, contact_id="c2", email="b@b.com", cadence_months=6)
    # Contact recently touched (not overdue)
    seed_contact(conn, contact_id="c3", email="c@c.com", cadence_months=3)
    recent_date = (date.today() - timedelta(days=10)).isoformat()
    db.upsert_note(conn, {
        "id": "n2", "contact_id": "c3", "text": "Recent",
        "date": recent_date, "author_id": "u1"
    })
    conn.commit()
    overdue = db.list_overdue_contacts(conn)
    ids = {r["id"] for r in overdue}
    assert "c1" in ids  # past cadence
    assert "c2" in ids  # no history
    assert "c3" not in ids  # recently touched


def test_get_contact_history():
    conn = make_conn()
    seed_contact(conn)
    db.upsert_note(conn, {
        "id": "n1", "contact_id": "c1", "text": "Called about deal",
        "date": "2026-01-10", "author_id": "u1"
    })
    db.upsert_email(conn, {
        "id": "e1", "contact_id": "c1", "subject": "Proposal",
        "body_preview": "Hi Peter, sending proposal...", "date": "2026-02-01",
        "direction": "in", "thread_id": "t1",
        "from_address": "alice@acme.com", "to_addresses": "[]"
    })
    conn.commit()
    history = db.get_contact_history(conn, "c1", limit=10)
    assert len(history) == 2
    assert history[0]["date"] == "2026-02-01"   # most recent first
    assert history[0]["type"] == "email"
    assert history[1]["type"] == "note"


def test_get_sync_status():
    conn = make_conn()
    db.log_sync(conn, "opcrm", contacts_synced=5, records_synced=30)
    conn.commit()
    status = db.get_sync_status(conn)
    assert status["opcrm"]["contacts_synced"] == 5
```

- [ ] **Step 2: Run new tests to confirm they fail**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
python -m pytest tests/test_db.py -v -k "test_get or test_search or test_list"
```

Expected: all fail with `AttributeError: module 'db' has no attribute 'get_contact_by_id'`

- [ ] **Step 3: Add query functions to db.py**

Append to `opcrm-mcp/db.py`:
```python
# ── Query functions ────────────────────────────────────────────────────────────

def get_contact_by_id(conn, contact_id):
    row = conn.execute(
        "SELECT * FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["tags"] = [
        r[0] for r in conn.execute(
            "SELECT tag FROM contact_tags WHERE contact_id = ?", (contact_id,)
        ).fetchall()
    ]
    return result


def search_contacts(conn, query):
    pattern = f"%{query}%"
    rows = conn.execute("""
        SELECT * FROM contacts
        WHERE name LIKE ? OR company LIKE ? OR email LIKE ?
        ORDER BY name
    """, (pattern, pattern, pattern)).fetchall()
    return [dict(r) for r in rows]


def list_contacts_by_tag(conn, tag):
    rows = conn.execute("""
        SELECT c.* FROM contacts c
        JOIN contact_tags t ON t.contact_id = c.id
        WHERE t.tag = ?
        ORDER BY c.name
    """, (tag,)).fetchall()
    return [dict(r) for r in rows]


def list_contacts_by_owner(conn, owner_id):
    rows = conn.execute(
        "SELECT * FROM contacts WHERE owner_id = ? ORDER BY name", (owner_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def list_overdue_contacts(conn):
    """
    Returns contacts where last touch across all channels exceeds cadence window,
    or contacts with no interaction history at all.
    cadence_months is converted to days using 30.44 days/month.
    """
    rows = conn.execute("""
        WITH last_touch AS (
            SELECT contact_id, MAX(date) AS last_date FROM (
                SELECT contact_id, date FROM notes
                UNION ALL SELECT contact_id, date FROM calls
                UNION ALL SELECT contact_id, date FROM meetings
                UNION ALL SELECT contact_id, date FROM emails
            ) GROUP BY contact_id
        )
        SELECT c.id, c.name, c.company, c.owner_id, c.cadence_months,
               lt.last_date,
               CAST(julianday('now') - julianday(COALESCE(lt.last_date, '2000-01-01')) AS INTEGER)
                   AS days_since
        FROM contacts c
        LEFT JOIN last_touch lt ON lt.contact_id = c.id
        WHERE julianday('now') - julianday(COALESCE(lt.last_date, '2000-01-01'))
              > c.cadence_months * 30.44
        ORDER BY days_since DESC
    """).fetchall()
    return [dict(r) for r in rows]


def get_contact_history(conn, contact_id, limit=50):
    """Unified timeline of notes, calls, meetings, and emails for a contact."""
    rows = conn.execute("""
        SELECT 'note'    AS type, date, text    AS content, NULL AS subject,
               NULL      AS direction, author_id AS from_address
        FROM notes WHERE contact_id = ?
        UNION ALL
        SELECT 'call'    AS type, date, text    AS content, NULL AS subject,
               NULL      AS direction, author_id AS from_address
        FROM calls WHERE contact_id = ?
        UNION ALL
        SELECT 'meeting' AS type, date, text    AS content, NULL AS subject,
               NULL      AS direction, author_id AS from_address
        FROM meetings WHERE contact_id = ?
        UNION ALL
        SELECT 'email'   AS type, date, body_preview AS content, subject,
               direction, from_address
        FROM emails WHERE contact_id = ?
        ORDER BY date DESC
        LIMIT ?
    """, (contact_id, contact_id, contact_id, contact_id, limit)).fetchall()
    return [dict(r) for r in rows]


def get_recent_emails(conn, contact_id, limit=20):
    rows = conn.execute("""
        SELECT * FROM emails WHERE contact_id = ?
        ORDER BY date DESC LIMIT ?
    """, (contact_id, limit)).fetchall()
    return [dict(r) for r in rows]


def get_notes(conn, contact_id):
    rows = conn.execute(
        "SELECT * FROM notes WHERE contact_id = ? ORDER BY date DESC",
        (contact_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_sync_status(conn):
    rows = conn.execute("""
        SELECT source,
               MAX(last_synced_at) AS last_synced_at,
               contacts_synced, records_synced, error
        FROM sync_log GROUP BY source
    """).fetchall()
    return {row["source"]: dict(row) for row in rows}


def get_all_tags(conn):
    rows = conn.execute(
        "SELECT DISTINCT tag FROM contact_tags ORDER BY tag"
    ).fetchall()
    return [r[0] for r in rows]
```

- [ ] **Step 4: Run all db tests and confirm they pass**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
python -m pytest tests/test_db.py -v
```

Expected: all tests pass (original 6 + new query tests).

- [ ] **Step 5: Commit**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot
git add opcrm-mcp/db.py opcrm-mcp/tests/test_db.py
git commit -m "feat: read query functions for contacts, history, and overdue detection"
```

---

## Task 8: Write Tool Helpers — Two-Phase Pattern and Ownership Checks

**Files:**
- Create: `opcrm-mcp/tests/test_server.py`
- Create: `opcrm-mcp/write_helpers.py`

The two-phase pattern: every write function accepts `confirmed=False`. When false, it returns a dict with `{"preview": "...", "confirmation_required": True}` and does nothing. When true, it executes.

- [ ] **Step 1: Write failing tests**

`opcrm-mcp/tests/test_server.py`:
```python
import json
import sqlite3
import sys
import os
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db
import write_helpers

CONFIG = {
    "opcrm_user_id": "uid", "opcrm_api_key": "key",
    "opcrm_my_user_id": "uid",
    "graph_client_id": "c", "graph_tenant_id": "t",
    "graph_access_token": "", "graph_refresh_token": "", "graph_token_expiry": 0
}


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    return conn


def seed_contact(conn, contact_id="c1", owner_id="uid"):
    db.upsert_contact(conn, {
        "id": contact_id, "name": "Alice Smith", "company": "Acme",
        "email": "alice@acme.com", "phone": "", "owner_id": owner_id,
        "status": "active", "cadence_months": 6, "raw_json": "{}"
    })
    conn.commit()


# ── log_note ──────────────────────────────────────────────────────────────────

def test_log_note_preview_does_not_write():
    conn = make_conn()
    seed_contact(conn)
    result = write_helpers.log_note(CONFIG, conn, "c1", "Follow up", confirmed=False)
    assert result["confirmation_required"] is True
    assert "Follow up" in result["preview"]
    count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    assert count == 0


def test_log_note_confirmed_calls_api_and_updates_db():
    conn = make_conn()
    seed_contact(conn)
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": {"note": {"id": "n_new", "contact_id": "c1",
                          "text": "Follow up", "date": "2026-03-31", "author_id": "uid"}}
    }
    mock_response.raise_for_status = MagicMock()
    with patch("requests.post", return_value=mock_response):
        result = write_helpers.log_note(CONFIG, conn, "c1", "Follow up", confirmed=True)
    assert result["success"] is True
    count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    assert count == 1


# ── create_next_action ────────────────────────────────────────────────────────

def test_create_action_preview_does_not_write():
    conn = make_conn()
    seed_contact(conn)
    result = write_helpers.create_next_action(
        CONFIG, conn, "c1", "Send proposal", "2026-04-15", confirmed=False
    )
    assert result["confirmation_required"] is True
    count = conn.execute("SELECT COUNT(*) FROM next_actions").fetchone()[0]
    assert count == 0


def test_create_action_on_unowned_contact_requires_extra_confirmation():
    conn = make_conn()
    seed_contact(conn, owner_id="other_user")  # not owned by CONFIG user
    result = write_helpers.create_next_action(
        CONFIG, conn, "c1", "Send proposal", "2026-04-15", confirmed=False
    )
    assert result["confirmation_required"] is True
    assert result.get("unowned_contact") is True


# ── tag operations ────────────────────────────────────────────────────────────

def test_add_tag_blocked_on_unowned_contact():
    conn = make_conn()
    seed_contact(conn, owner_id="other_user")
    result = write_helpers.add_tag(CONFIG, conn, "c1", "partner", confirmed=True)
    assert result["error"] is not None
    assert "not owned" in result["error"].lower()


def test_remove_tag_blocked_on_unowned_contact():
    conn = make_conn()
    seed_contact(conn, owner_id="other_user")
    result = write_helpers.remove_tag(CONFIG, conn, "c1", "investor", confirmed=True)
    assert result["error"] is not None


def test_add_tag_on_owned_contact_preview():
    conn = make_conn()
    seed_contact(conn, owner_id="uid")
    result = write_helpers.add_tag(CONFIG, conn, "c1", "partner", confirmed=False)
    assert result["confirmation_required"] is True
    assert result.get("error") is None


# ── update_cadence ────────────────────────────────────────────────────────────

def test_update_cadence_always_shows_preview_first():
    conn = make_conn()
    seed_contact(conn)
    result = write_helpers.update_cadence(CONFIG, conn, "c1", 12, confirmed=False)
    assert result["confirmation_required"] is True
    assert "current_cadence_months" in result


def test_update_cadence_blocked_on_unowned_contact():
    conn = make_conn()
    seed_contact(conn, owner_id="other_user")
    result = write_helpers.update_cadence(CONFIG, conn, "c1", 12, confirmed=True)
    assert result["error"] is not None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
python -m pytest tests/test_server.py -v
```

Expected: `ModuleNotFoundError: No module named 'write_helpers'`

- [ ] **Step 3: Write write_helpers.py**

`opcrm-mcp/write_helpers.py`:
```python
"""
Two-phase write helpers. All functions accept confirmed=False (preview) or
confirmed=True (execute). Tag and cadence operations on unowned contacts are
blocked at the server level regardless of confirmed value.
"""
import db
import opcrm


def _is_owner(config, contact_row):
    return contact_row["owner_id"] == config["opcrm_my_user_id"]


def log_note(config, conn, contact_id, text, confirmed=False):
    contact = db.get_contact_by_id(conn, contact_id)
    if contact is None:
        return {"error": f"Contact {contact_id} not found in local cache. Run sync first."}

    if not confirmed:
        return {
            "confirmation_required": True,
            "preview": f"Log note for {contact['name']}: \"{text}\"",
            "action": "log_note",
            "contact_id": contact_id,
            "contact_name": contact["name"],
        }

    result = opcrm.create_note(config, contact_id, text)
    note_data = result.get("data", {}).get("note", {})
    if note_data.get("id"):
        db.upsert_note(conn, {
            "id": note_data["id"],
            "contact_id": contact_id,
            "text": text,
            "date": note_data.get("date", ""),
            "author_id": note_data.get("author_id", ""),
        })
        conn.commit()
    return {"success": True, "note_id": note_data.get("id")}


def create_next_action(config, conn, contact_id, text, due_date, confirmed=False):
    contact = db.get_contact_by_id(conn, contact_id)
    if contact is None:
        return {"error": f"Contact {contact_id} not found. Run sync first."}

    owned = _is_owner(config, contact)

    if not confirmed:
        return {
            "confirmation_required": True,
            "unowned_contact": not owned,
            "preview": (
                f"Create next action for {contact['name']} (due {due_date}): \"{text}\""
                + ("" if owned else f"\n⚠ Note: this contact is owned by user {contact['owner_id']}, not you.")
            ),
            "action": "create_next_action",
        }

    result = opcrm.create_action(config, contact_id, text, due_date)
    action_data = result.get("data", {}).get("action", {})
    if action_data.get("id"):
        db.upsert_action(conn, {
            "id": action_data["id"],
            "contact_id": contact_id,
            "text": text,
            "due_date": due_date,
            "assignee_id": action_data.get("assignee_id", ""),
            "status": action_data.get("status", ""),
        })
        conn.commit()
    return {"success": True, "action_id": action_data.get("id")}


def complete_next_action(config, conn, action_id, confirmed=False):
    row = conn.execute(
        "SELECT na.*, c.name, c.owner_id FROM next_actions na "
        "JOIN contacts c ON c.id = na.contact_id WHERE na.id = ?", (action_id,)
    ).fetchone()
    if row is None:
        return {"error": f"Action {action_id} not found. Run sync first."}

    if not confirmed:
        return {
            "confirmation_required": True,
            "preview": f"Complete action for {row['name']}: \"{row['text']}\"",
            "action": "complete_next_action",
        }

    opcrm.complete_action(config, action_id)
    conn.execute("DELETE FROM next_actions WHERE id = ?", (action_id,))
    conn.commit()
    return {"success": True}


def reschedule_next_action(config, conn, action_id, new_date, confirmed=False):
    row = conn.execute(
        "SELECT na.*, c.name, c.owner_id FROM next_actions na "
        "JOIN contacts c ON c.id = na.contact_id WHERE na.id = ?", (action_id,)
    ).fetchone()
    if row is None:
        return {"error": f"Action {action_id} not found. Run sync first."}

    if not confirmed:
        return {
            "confirmation_required": True,
            "preview": (
                f"Reschedule action for {row['name']}: \"{row['text']}\"\n"
                f"From {row['due_date']} → {new_date}"
            ),
            "action": "reschedule_next_action",
        }

    opcrm.reschedule_action(config, action_id, new_date)
    conn.execute(
        "UPDATE next_actions SET due_date = ? WHERE id = ?", (new_date, action_id)
    )
    conn.commit()
    return {"success": True}


def update_cadence(config, conn, contact_id, months, confirmed=False):
    contact = db.get_contact_by_id(conn, contact_id)
    if contact is None:
        return {"error": f"Contact {contact_id} not found. Run sync first."}

    if not _is_owner(config, contact):
        return {"error": f"Cannot update cadence: contact {contact['name']} is not owned by you."}

    # Always preview first, regardless of confirmed flag
    if not confirmed:
        return {
            "confirmation_required": True,
            "current_cadence_months": contact["cadence_months"],
            "new_cadence_months": months,
            "preview": (
                f"Update cadence for {contact['name']}: "
                f"{contact['cadence_months']} months → {months} months.\n"
                f"⚠ This affects overdue detection in the CRM Co-Pilot browser app."
            ),
            "action": "update_cadence",
        }

    opcrm.update_cadence(config, contact_id, months)
    conn.execute(
        "UPDATE contacts SET cadence_months = ? WHERE id = ?", (months, contact_id)
    )
    conn.commit()
    return {"success": True}


def add_tag(config, conn, contact_id, tag, confirmed=False):
    contact = db.get_contact_by_id(conn, contact_id)
    if contact is None:
        return {"error": f"Contact {contact_id} not found. Run sync first."}

    if not _is_owner(config, contact):
        return {"error": f"Cannot add tag: contact {contact['name']} is not owned by you."}

    if not confirmed:
        return {
            "confirmation_required": True,
            "preview": f"Add tag \"{tag}\" to {contact['name']}",
            "action": "add_tag",
        }

    opcrm.add_tag(config, contact_id, tag)
    conn.execute(
        "INSERT OR IGNORE INTO contact_tags (contact_id, tag) VALUES (?, ?)",
        (contact_id, tag)
    )
    conn.commit()
    return {"success": True}


def remove_tag(config, conn, contact_id, tag, confirmed=False):
    contact = db.get_contact_by_id(conn, contact_id)
    if contact is None:
        return {"error": f"Contact {contact_id} not found. Run sync first."}

    if not _is_owner(config, contact):
        return {"error": f"Cannot remove tag: contact {contact['name']} is not owned by you."}

    if not confirmed:
        return {
            "confirmation_required": True,
            "preview": f"Remove tag \"{tag}\" from {contact['name']}",
            "action": "remove_tag",
        }

    opcrm.remove_tag(config, contact_id, tag)
    conn.execute(
        "DELETE FROM contact_tags WHERE contact_id = ? AND tag = ?", (contact_id, tag)
    )
    conn.commit()
    return {"success": True}
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
python -m pytest tests/test_server.py -v
```

Expected: all 10 tests pass.

- [ ] **Step 5: Commit**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot
git add opcrm-mcp/write_helpers.py opcrm-mcp/tests/test_server.py
git commit -m "feat: two-phase write helpers with ownership enforcement"
```

---

## Task 9: MCP Server — Wire Everything Together

**Files:**
- Create: `opcrm-mcp/server.py`

- [ ] **Step 1: Write server.py**

`opcrm-mcp/server.py`:
```python
"""
OnePageCRM Copilot MCP Server

Run with: python server.py
Configure Claude Desktop to connect via stdio.
"""
import json
import sqlite3
from mcp.server.fastmcp import FastMCP

import auth
import db
import sync as sync_module
import write_helpers

mcp = FastMCP("OnePageCRM Copilot")

# Load config and open persistent DB connection at startup
_config = auth.load_config()
db.init_db()
_conn = db.get_conn()


# ── Sync tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def sync() -> dict:
    """
    Sync all data from OnePageCRM and Outlook into the local cache.
    Run this before analysis sessions or when you want fresh data.
    Returns a summary of what was synced.
    """
    return sync_module.full_sync(_config)


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
        from datetime import date
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
        from datetime import date
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
                     "days_since_last_touch": days_since}
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
```

- [ ] **Step 2: Verify server starts without error**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
python server.py &
```

Expected: server starts silently (stdio mode, waits for input). Kill with Ctrl+C. If you see a Python import error, fix the import before proceeding.

- [ ] **Step 3: Run all tests to confirm nothing broke**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot
git add opcrm-mcp/server.py
git commit -m "feat: FastMCP server with all read and write tools"
```

---

## Task 10: Connect to Claude Desktop

**Files:**
- Modify: `C:\Users\PeterMoon\AppData\Roaming\Claude\claude_desktop_config.json`

- [ ] **Step 1: Open Claude Desktop config**

The config file is at:
```
C:\Users\PeterMoon\AppData\Roaming\Claude\claude_desktop_config.json
```

If it doesn't exist, create it. If it already has an `mcpServers` section, add to it.

- [ ] **Step 2: Add the MCP server entry**

Add this block under `mcpServers`:
```json
{
  "mcpServers": {
    "opcrm-copilot": {
      "command": "python",
      "args": ["H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp/server.py"],
      "cwd": "H:/ClaudeCode/OnePageCRM-Copilot/opcrm-mcp"
    }
  }
}
```

If Python is not on your PATH for Claude Desktop's process, use the full path to python.exe:
```bash
where python
```
Replace `"python"` with the full path if needed (e.g., `"C:/Users/PeterMoon/AppData/Local/Programs/Python/Python311/python.exe"`).

- [ ] **Step 3: Restart Claude Desktop**

Fully quit Claude Desktop (system tray → Quit, not just close window) and reopen it.

- [ ] **Step 4: Verify the server is connected**

In a new Claude conversation, you should see the `opcrm-copilot` server listed in the tools panel. Ask Claude:

> "Call sync_status() and tell me what's in the cache."

Expected: Claude calls `sync_status` and returns record counts (all zeros on first run — that's correct).

- [ ] **Step 5: Run first sync**

Tell Claude:

> "Run sync() to pull my OnePageCRM and Outlook data."

On first run, the Graph sync will trigger the device-code flow — Claude will surface the URL and code from the server's stdout. Go to the URL, enter the code, and approve access. Sync will complete once authenticated.

Expected: sync returns `{"contacts_synced": N, "records_synced": M, "emails_synced": K}`.

- [ ] **Step 6: Commit final state**

```bash
cd H:/ClaudeCode/OnePageCRM-Copilot
git add -A
git commit -m "feat: complete OnePageCRM MCP server"
```

---

## Test Run Checklist

After Task 10 is complete, verify these queries work in Claude:

- [ ] `sync_status()` — returns table counts
- [ ] `list_overdue_contacts()` — returns contacts past cadence
- [ ] `get_contact("Alice Smith")` — returns full profile + next action
- [ ] `get_contact_history("<contact_id>")` — returns unified timeline
- [ ] `cluster_contacts("tag")` — groups contacts by tag
- [ ] `log_note("<id>", "Test note", confirmed=False)` — returns preview, no write
- [ ] `log_note("<id>", "Test note", confirmed=True)` — note appears in OPCRM
- [ ] `add_tag("<unowned_id>", "test", confirmed=True)` — returns error about ownership
