# Personal Mailbox Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add pmoon@live.com as a second synced mailbox so personal-account emails surface in `get_actionable_emails` and in CRM contact history alongside work emails.

**Architecture:** A second Graph token set (personal) is stored in `config.json` and authenticated via a `consumers` OAuth endpoint. `auth.py` internals are parameterized so both flows share code. A `mailbox` column is added to the `emails` and `unmatched_emails` tables; sync runs against both accounts and tags rows accordingly. `sync_emails` MCP tool runs both syncs automatically when personal tokens are configured.

**Tech Stack:** Python, SQLite (via existing `db.py`), Microsoft Graph API, MCP (FastMCP), `requests`, `pytest`

---

## File Map

| File | Change |
|---|---|
| `opcrm-mcp/auth.py` | Refactor internals to accept endpoint+key params; add `get_graph_token_personal()` |
| `opcrm-mcp/db.py` | Add `mailbox` column to schema + migration; update upserts; update `get_actionable_emails` |
| `opcrm-mcp/sync.py` | Add `mailbox` param to `_process_email_batch`; add `sync_graph_personal()` |
| `opcrm-mcp/server.py` | Add personal auth tools; update `sync_emails` to run both accounts |
| `opcrm-mcp/tests/test_auth.py` | Tests for personal token flow and refactored work account flow |
| `opcrm-mcp/tests/test_db.py` | Tests for mailbox column, migration, upserts, `get_actionable_emails` |
| `opcrm-mcp/tests/test_sync.py` | Tests for `_process_email_batch` mailbox param and `sync_graph_personal` |

---

## Task 1: Refactor auth.py — parameterize internals and add personal token support

**Files:**
- Modify: `opcrm-mcp/auth.py`
- Modify: `opcrm-mcp/tests/test_auth.py`

- [ ] **Step 1: Write failing tests**

Add to `opcrm-mcp/tests/test_auth.py`:

```python
def test_get_graph_token_work_still_uses_tenant_endpoint(tmp_path):
    """After refactor, work account must still hit the tenant-specific endpoint."""
    config, path = make_config(tmp_path)
    config["graph_refresh_token"] = "work_refresh"
    config["graph_token_expiry"] = time.time() - 1

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "access_token": "new_work_token",
        "refresh_token": "new_refresh",
        "expires_in": 3600,
    }
    mock_response.raise_for_status = MagicMock()

    with patch("requests.post", return_value=mock_response) as mock_post:
        token = auth.get_graph_token(config, path)

    assert token == "new_work_token"
    call_url = mock_post.call_args[0][0]
    assert "tenant456" in call_url
    assert "consumers" not in call_url


def test_get_graph_token_personal_returns_valid_cached_token(tmp_path):
    config, path = make_config(tmp_path)
    config["graph_access_token_personal"] = "personal_tok"
    config["graph_token_expiry_personal"] = time.time() + 3600
    token = auth.get_graph_token_personal(config, path)
    assert token == "personal_tok"


def test_get_graph_token_personal_refreshes_expired_token(tmp_path):
    config, path = make_config(tmp_path)
    config["graph_access_token_personal"] = "old"
    config["graph_refresh_token_personal"] = "personal_refresh"
    config["graph_token_expiry_personal"] = time.time() - 1

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "access_token": "new_personal_token",
        "refresh_token": "new_personal_refresh",
        "expires_in": 3600,
    }
    mock_response.raise_for_status = MagicMock()

    with patch("requests.post", return_value=mock_response) as mock_post:
        token = auth.get_graph_token_personal(config, path)

    assert token == "new_personal_token"
    call_url = mock_post.call_args[0][0]
    assert "consumers" in call_url
    assert "tenant456" not in call_url
    call_data = mock_post.call_args[1]["data"]
    assert call_data["grant_type"] == "refresh_token"
    assert call_data["refresh_token"] == "personal_refresh"

    saved = auth.load_config(path)
    assert saved["graph_access_token_personal"] == "new_personal_token"
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd opcrm-mcp
pytest tests/test_auth.py::test_get_graph_token_work_still_uses_tenant_endpoint tests/test_auth.py::test_get_graph_token_personal_returns_valid_cached_token tests/test_auth.py::test_get_graph_token_personal_refreshes_expired_token -v
```

Expected: FAIL — `get_graph_token_personal` does not exist yet.

- [ ] **Step 3: Rewrite auth.py**

Replace the entire contents of `opcrm-mcp/auth.py` with:

```python
import json
import time
import requests
from pathlib import Path

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.json"

_WORK_SCOPE = "Mail.Read Calendars.Read User.Read offline_access"
_PERSONAL_SCOPE = "Mail.Read User.Read offline_access"
_CONSUMERS_ENDPOINT = "https://login.microsoftonline.com/consumers/oauth2/v2.0"


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


def _client_params(config):
    """Base params included in all token requests — adds secret if configured."""
    params = {"client_id": config["graph_client_id"]}
    secret = config.get("graph_client_secret", "")
    if secret:
        params["client_secret"] = secret
    return params


def _work_flow_params(config):
    """OAuth params for the work account (tenant-specific endpoint)."""
    endpoint = f"https://login.microsoftonline.com/{config['graph_tenant_id']}/oauth2/v2.0"
    return {
        "endpoint": endpoint,
        "token_key": "graph_access_token",
        "refresh_key": "graph_refresh_token",
        "expiry_key": "graph_token_expiry",
        "scope": _WORK_SCOPE,
    }


def _personal_flow_params():
    """OAuth params for the personal account (consumers endpoint)."""
    return {
        "endpoint": _CONSUMERS_ENDPOINT,
        "token_key": "graph_access_token_personal",
        "refresh_key": "graph_refresh_token_personal",
        "expiry_key": "graph_token_expiry_personal",
        "scope": _PERSONAL_SCOPE,
    }


def _refresh_token(config, config_path, *, endpoint, token_key, refresh_key, expiry_key, scope):
    r = requests.post(
        f"{endpoint}/token",
        data={
            **_client_params(config),
            "grant_type": "refresh_token",
            "refresh_token": config[refresh_key],
            "scope": scope,
        },
    )
    r.raise_for_status()
    data = r.json()
    config[token_key] = data["access_token"]
    config[refresh_key] = data.get("refresh_token", config[refresh_key])
    config[expiry_key] = time.time() + data.get("expires_in", 3600)
    save_config(config, config_path)
    return config[token_key]


def _run_device_flow(config, config_path, *, endpoint, token_key, refresh_key, expiry_key, scope):
    r = requests.post(
        f"{endpoint}/devicecode",
        data={
            "client_id": config["graph_client_id"],
            "scope": scope,
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
            f"{endpoint}/token",
            data={
                "client_id": config["graph_client_id"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": flow["device_code"],
            },
        )
        data = poll.json()
        if "access_token" in data:
            config[token_key] = data["access_token"]
            config[refresh_key] = data.get("refresh_token", "")
            config[expiry_key] = time.time() + data.get("expires_in", 3600)
            save_config(config, config_path)
            print("Outlook authentication successful.")
            return config[token_key]
        if data.get("error") == "slow_down":
            interval += 5
        elif data.get("error") != "authorization_pending":
            raise RuntimeError(
                f"Device flow failed: {data.get('error_description', data.get('error'))}"
            )

    raise RuntimeError("Device flow timed out. Re-run sync to try again.")


def _get_token(config, config_path, params):
    """Core token retrieval logic — shared by work and personal flows."""
    token_key = params["token_key"]
    expiry_key = params["expiry_key"]
    refresh_key = params["refresh_key"]

    if config.get(token_key) and config.get(expiry_key, 0) > time.time() + 60:
        return config[token_key]

    # Re-read disk in case an external auth script updated tokens without restarting the server
    path = Path(config_path or _DEFAULT_CONFIG_PATH)
    if path.exists():
        fresh = json.load(open(path))
        if fresh.get(token_key) and fresh.get(expiry_key, 0) > time.time() + 60:
            config.update(fresh)
            return config[token_key]
        if fresh.get(refresh_key) and not config.get(refresh_key):
            config.update(fresh)

    if config.get(refresh_key):
        return _refresh_token(config, config_path, **params)
    return _run_device_flow(config, config_path, **params)


def get_graph_token(config, config_path=None):
    """Return a valid Graph access token for the work account. Refreshes or runs device flow as needed."""
    return _get_token(config, config_path, _work_flow_params(config))


def get_graph_token_personal(config, config_path=None):
    """Return a valid Graph access token for the personal account (consumers endpoint)."""
    return _get_token(config, config_path, _personal_flow_params())
```

- [ ] **Step 4: Run all auth tests**

```
cd opcrm-mcp
pytest tests/test_auth.py -v
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add opcrm-mcp/auth.py opcrm-mcp/tests/test_auth.py
git commit -m "feat: parameterize auth internals and add get_graph_token_personal"
```

---

## Task 2: Add mailbox column to DB

**Files:**
- Modify: `opcrm-mcp/db.py`
- Modify: `opcrm-mcp/tests/test_db.py`

- [ ] **Step 1: Write failing tests**

Add to `opcrm-mcp/tests/test_db.py` (after existing imports, add `import sqlite3` if not present):

```python
def test_emails_table_has_mailbox_column():
    conn = make_conn()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(emails)").fetchall()}
    assert "mailbox" in cols


def test_unmatched_emails_table_has_mailbox_column():
    conn = make_conn()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(unmatched_emails)").fetchall()}
    assert "mailbox" in cols


def test_upsert_email_stores_mailbox():
    conn = make_conn()
    db.upsert_contact(conn, {
        "id": "c1", "name": "Alice", "company": "", "email": "alice@acme.com",
        "phone": "", "owner_id": "u1", "status": "active", "cadence_months": 6, "raw_json": "{}"
    })
    db.upsert_email(conn, {
        "id": "e1", "contact_id": "c1", "subject": "Hi", "body_preview": "hey",
        "date": "2026-01-01T00:00:00Z", "direction": "in", "thread_id": "t1",
        "from_address": "alice@acme.com", "to_addresses": "[]", "mailbox": "personal"
    })
    conn.commit()
    row = conn.execute("SELECT mailbox FROM emails WHERE id = 'e1'").fetchone()
    assert row[0] == "personal"


def test_upsert_email_defaults_mailbox_to_work():
    conn = make_conn()
    db.upsert_contact(conn, {
        "id": "c1", "name": "Alice", "company": "", "email": "alice@acme.com",
        "phone": "", "owner_id": "u1", "status": "active", "cadence_months": 6, "raw_json": "{}"
    })
    db.upsert_email(conn, {
        "id": "e1", "contact_id": "c1", "subject": "Hi", "body_preview": "hey",
        "date": "2026-01-01T00:00:00Z", "direction": "in", "thread_id": "t1",
        "from_address": "alice@acme.com", "to_addresses": "[]"
        # no mailbox key — should default to 'work'
    })
    conn.commit()
    row = conn.execute("SELECT mailbox FROM emails WHERE id = 'e1'").fetchone()
    assert row[0] == "work"


def test_upsert_unmatched_email_stores_mailbox():
    conn = make_conn()
    db.upsert_unmatched_email(conn, {
        "id": "u1", "subject": "Invoice", "body_preview": "attached",
        "date": "2026-01-01T00:00:00Z", "direction": "in",
        "from_address": "vendor@example.com", "to_addresses": "[]",
        "conversation_id": "conv1", "mailbox": "personal"
    })
    conn.commit()
    row = conn.execute("SELECT mailbox FROM unmatched_emails WHERE id = 'u1'").fetchone()
    assert row[0] == "personal"


def test_get_actionable_emails_returns_mailbox():
    conn = make_conn()
    db.upsert_unmatched_email(conn, {
        "id": "u1", "subject": "Invoice", "body_preview": "attached",
        "date": "2026-04-01T00:00:00Z", "direction": "in",
        "from_address": "vendor@example.com", "to_addresses": "[]",
        "conversation_id": "conv1", "mailbox": "personal"
    })
    conn.commit()
    results = db.get_actionable_emails(conn, since="2026-01-01T00:00:00Z")
    assert len(results) == 1
    assert results[0]["mailbox"] == "personal"


def test_mailbox_migration_adds_column_to_existing_db():
    """Verify init_db adds mailbox via ALTER TABLE on a DB created without it."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE emails (
            id TEXT PRIMARY KEY, contact_id TEXT NOT NULL, subject TEXT,
            body_preview TEXT, date TEXT, direction TEXT,
            thread_id TEXT, from_address TEXT, to_addresses TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE unmatched_emails (
            id TEXT PRIMARY KEY, subject TEXT, body_preview TEXT, date TEXT,
            direction TEXT, from_address TEXT, to_addresses TEXT, conversation_id TEXT
        )
    """)
    conn.commit()
    db.init_db(conn)
    email_cols = {row[1] for row in conn.execute("PRAGMA table_info(emails)").fetchall()}
    unmatched_cols = {row[1] for row in conn.execute("PRAGMA table_info(unmatched_emails)").fetchall()}
    assert "mailbox" in email_cols
    assert "mailbox" in unmatched_cols
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd opcrm-mcp
pytest tests/test_db.py::test_emails_table_has_mailbox_column tests/test_db.py::test_upsert_email_stores_mailbox tests/test_db.py::test_get_actionable_emails_returns_mailbox tests/test_db.py::test_mailbox_migration_adds_column_to_existing_db -v
```

Expected: FAIL — mailbox column does not exist yet.

- [ ] **Step 3: Update SCHEMA in db.py — add mailbox to both table definitions**

In the `SCHEMA` string, find the `emails` table definition and add the `mailbox` column:

```sql
CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL,
    subject TEXT,
    body_preview TEXT,
    date TEXT,
    direction TEXT,
    thread_id TEXT,
    from_address TEXT,
    to_addresses TEXT,
    mailbox TEXT DEFAULT 'work',
    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);
```

And the `unmatched_emails` table:

```sql
CREATE TABLE IF NOT EXISTS unmatched_emails (
    id TEXT PRIMARY KEY,
    subject TEXT,
    body_preview TEXT,
    date TEXT,
    direction TEXT,
    from_address TEXT,
    to_addresses TEXT,
    conversation_id TEXT,
    mailbox TEXT DEFAULT 'work'
);
```

- [ ] **Step 4: Add migration to init_db**

In `init_db`, after `conn.executescript(SCHEMA)` and before the calendar migration, add:

```python
# Migrate: add mailbox column to emails and unmatched_emails if not present
for table in ("emails", "unmatched_emails"):
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN mailbox TEXT DEFAULT 'work'")
    except sqlite3.OperationalError:
        pass  # column already exists
```

- [ ] **Step 5: Update upsert_email to include mailbox**

Replace `upsert_email` with:

```python
def upsert_email(conn, e):
    row = {**e, "mailbox": e.get("mailbox", "work")}
    conn.execute("""
        INSERT INTO emails (id, contact_id, subject, body_preview, date,
                            direction, thread_id, from_address, to_addresses, mailbox)
        VALUES (:id, :contact_id, :subject, :body_preview, :date,
                :direction, :thread_id, :from_address, :to_addresses, :mailbox)
        ON CONFLICT(id) DO UPDATE SET
            subject=excluded.subject, body_preview=excluded.body_preview,
            date=excluded.date, direction=excluded.direction,
            mailbox=excluded.mailbox
    """, row)
```

- [ ] **Step 6: Update upsert_unmatched_email to include mailbox**

Replace `upsert_unmatched_email` with:

```python
def upsert_unmatched_email(conn, e):
    row = {**e, "mailbox": e.get("mailbox", "work")}
    conn.execute("""
        INSERT INTO unmatched_emails
            (id, subject, body_preview, date, direction, from_address, to_addresses, conversation_id, mailbox)
        VALUES
            (:id, :subject, :body_preview, :date, :direction, :from_address, :to_addresses, :conversation_id, :mailbox)
        ON CONFLICT(id) DO UPDATE SET
            subject=excluded.subject, date=excluded.date, mailbox=excluded.mailbox
    """, row)
```

- [ ] **Step 7: Update get_actionable_emails to return mailbox**

In `get_actionable_emails`, update the SELECT line from:

```python
        SELECT id, subject, body_preview, date, from_address, direction
```

to:

```python
        SELECT id, subject, body_preview, date, from_address, direction, mailbox
```

- [ ] **Step 8: Run all DB tests**

```
cd opcrm-mcp
pytest tests/test_db.py -v
```

Expected: All pass.

- [ ] **Step 9: Commit**

```bash
git add opcrm-mcp/db.py opcrm-mcp/tests/test_db.py
git commit -m "feat: add mailbox column to emails and unmatched_emails with migration"
```

---

## Task 3: Add personal sync to sync.py

**Files:**
- Modify: `opcrm-mcp/sync.py`
- Modify: `opcrm-mcp/tests/test_sync.py`

- [ ] **Step 1: Write failing tests**

Add to `opcrm-mcp/tests/test_sync.py`:

```python
def test_process_email_batch_tags_mailbox_personal():
    conn = make_db()
    db.upsert_contact(conn, {
        "id": "c1", "name": "Jorge DN", "company": "Designs Northwest",
        "email": "jorge@designsnw.com", "phone": "", "owner_id": "uid",
        "status": "active", "cadence_months": 6, "raw_json": "{}"
    })
    conn.commit()
    email_map = {"jorge@designsnw.com": "c1"}
    msgs = [{
        "id": "msg1", "subject": "Schedule update", "bodyPreview": "Hi Peter",
        "receivedDateTime": "2026-03-15T10:00:00Z", "isDraft": False,
        "from": {"emailAddress": {"address": "jorge@designsnw.com"}},
        "toRecipients": [{"emailAddress": {"address": "pmoon@live.com"}}],
        "conversationId": "conv1",
    }]
    sync._process_email_batch(msgs, email_map, conn, mailbox="personal")
    conn.commit()
    row = conn.execute("SELECT mailbox FROM emails WHERE id = 'msg1'").fetchone()
    assert row[0] == "personal"


def test_process_email_batch_defaults_mailbox_to_work():
    conn = make_db()
    db.upsert_contact(conn, {
        "id": "c1", "name": "Alice Smith", "company": "Acme",
        "email": "alice@acme.com", "phone": "", "owner_id": "uid",
        "status": "active", "cadence_months": 6, "raw_json": "{}"
    })
    conn.commit()
    email_map = {"alice@acme.com": "c1"}
    msgs = [{
        "id": "msg2", "subject": "Hi", "bodyPreview": "hey",
        "receivedDateTime": "2026-03-01T10:00:00Z", "isDraft": False,
        "from": {"emailAddress": {"address": "alice@acme.com"}},
        "toRecipients": [{"emailAddress": {"address": "pmoon@navicet.com"}}],
        "conversationId": "conv2",
    }]
    sync._process_email_batch(msgs, email_map, conn)  # no mailbox arg
    conn.commit()
    row = conn.execute("SELECT mailbox FROM emails WHERE id = 'msg2'").fetchone()
    assert row[0] == "work"


PERSONAL_CONFIG = {
    **CONFIG,
    "graph_access_token_personal": "personal_tok",
    "graph_token_expiry_personal": 9999999999,
    "graph_refresh_token_personal": "personal_refresh",
}


def test_sync_graph_personal_raises_without_tokens():
    conn = make_db()
    config = {k: v for k, v in CONFIG.items()
              if not k.startswith("graph_access_token_personal")
              and not k.startswith("graph_refresh_token_personal")}
    try:
        sync.sync_graph_personal(config, conn=conn)
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "Personal Outlook not authorized" in str(e)


def test_sync_graph_personal_logs_to_graph_personal_source():
    conn = make_db()
    with patch("auth.get_graph_token_personal", return_value="personal_tok"), \
         patch("graph.fetch_emails", return_value=[]):
        sync.sync_graph_personal(PERSONAL_CONFIG, conn=conn)
    conn.commit()
    row = conn.execute(
        "SELECT source FROM sync_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row[0] == "graph_personal"


def test_sync_graph_personal_stores_personal_mailbox():
    conn = make_db()
    db.upsert_contact(conn, {
        "id": "c1", "name": "Jorge DN", "company": "Designs Northwest",
        "email": "jorge@designsnw.com", "phone": "", "owner_id": "uid",
        "status": "active", "cadence_months": 6, "raw_json": "{}"
    })
    conn.commit()

    personal_email = {
        "id": "pmsg1", "subject": "Schedule", "bodyPreview": "Hi Peter",
        "receivedDateTime": "2026-03-15T10:00:00Z", "isDraft": False,
        "from": {"emailAddress": {"address": "jorge@designsnw.com"}},
        "toRecipients": [{"emailAddress": {"address": "pmoon@live.com"}}],
        "conversationId": "pconv1",
    }

    with patch("auth.get_graph_token_personal", return_value="personal_tok"), \
         patch("graph.fetch_emails", return_value=[personal_email]):
        sync.sync_graph_personal(PERSONAL_CONFIG, conn=conn)

    row = conn.execute("SELECT mailbox FROM emails WHERE id = 'pmsg1'").fetchone()
    assert row[0] == "personal"
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd opcrm-mcp
pytest tests/test_sync.py::test_process_email_batch_tags_mailbox_personal tests/test_sync.py::test_sync_graph_personal_raises_without_tokens tests/test_sync.py::test_sync_graph_personal_logs_to_graph_personal_source -v
```

Expected: FAIL — `_process_email_batch` doesn't accept `mailbox`, `sync_graph_personal` doesn't exist.

- [ ] **Step 3: Update _process_email_batch to accept mailbox param**

Change the function signature and its two upsert calls in `opcrm-mcp/sync.py`:

```python
def _process_email_batch(msgs, email_map, conn, mailbox='work'):
    """Match a list of Graph messages to contacts; store matched and unmatched. Returns (matched, unmatched) counts."""
    matched = unmatched = 0
    for msg in msgs:
        from_addr = msg.get("from", {}).get("emailAddress", {}).get("address", "").lower()
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

        if contact_id:
            db.upsert_email(conn, {
                "id": msg["id"],
                "contact_id": contact_id,
                "subject": msg.get("subject", ""),
                "body_preview": msg.get("bodyPreview", ""),
                "date": msg.get("receivedDateTime", ""),
                "direction": direction,
                "thread_id": msg.get("conversationId", ""),
                "from_address": from_addr,
                "to_addresses": json.dumps(to_addrs),
                "mailbox": mailbox,
            })
            matched += 1
        else:
            db.upsert_unmatched_email(conn, {
                "id": msg["id"],
                "subject": msg.get("subject", ""),
                "body_preview": msg.get("bodyPreview", ""),
                "date": msg.get("receivedDateTime", ""),
                "direction": "in" if from_addr and from_addr not in to_addrs else "out",
                "from_address": from_addr,
                "to_addresses": json.dumps(to_addrs),
                "conversation_id": msg.get("conversationId", ""),
                "mailbox": mailbox,
            })
            unmatched += 1
    return matched, unmatched
```

- [ ] **Step 4: Add sync_graph_personal to sync.py**

Add after the closing `finally` block of `sync_graph`:

```python
def sync_graph_personal(config, conn=None, since_date=None):
    """
    Sync personal Outlook emails (pmoon@live.com) incrementally. Uses last sync timestamp if since_date not given.
    Matched emails go to emails table with mailbox='personal'; unmatched go to unmatched_emails.
    """
    import time
    if not (config.get("graph_access_token_personal")
            and config.get("graph_token_expiry_personal", 0) > time.time() + 60):
        if not config.get("graph_refresh_token_personal"):
            raise RuntimeError(
                "Personal Outlook not authorized. Call start_graph_auth_personal() "
                "then complete_graph_auth_personal() first."
            )
    token = auth.get_graph_token_personal(config)

    owns_conn = conn is None
    if owns_conn:
        conn = db.get_conn()
        db.init_db(conn)

    try:
        if since_date is None:
            last = db.get_last_sync_time(conn, "graph_personal")
            if last:
                since_date = last.replace(" ", "T") + "Z"
            else:
                dt = datetime.now(timezone.utc) - timedelta(days=90)
                since_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        rows = conn.execute("SELECT id, email FROM contacts WHERE email != ''").fetchall()
        email_map = {row["email"].lower(): row["id"] for row in rows}

        seen_ids = set()
        all_msgs = []
        for folder in ["me/messages", "me/mailFolders/sentItems/messages"]:
            for msg in graph.fetch_emails(token, since_date=since_date, folder=folder):
                if msg["id"] not in seen_ids:
                    seen_ids.add(msg["id"])
                    all_msgs.append(msg)

        matched, unmatched = _process_email_batch(all_msgs, email_map, conn, mailbox="personal")

        db.log_sync(conn, "graph_personal", 0, matched + unmatched)
        conn.commit()
        return {"emails_matched": matched, "emails_unmatched": unmatched, "since": since_date, "error": None}

    except Exception as e:
        db.log_sync(conn, "graph_personal", 0, 0, str(e))
        conn.commit()
        raise
    finally:
        if owns_conn:
            conn.close()
```

- [ ] **Step 5: Run all sync tests**

```
cd opcrm-mcp
pytest tests/test_sync.py -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add opcrm-mcp/sync.py opcrm-mcp/tests/test_sync.py
git commit -m "feat: add mailbox param to _process_email_batch and sync_graph_personal"
```

---

## Task 4: Add personal auth tools to server.py and update sync_emails

**Files:**
- Modify: `opcrm-mcp/server.py`

No unit tests for server MCP tools — the logic is a thin delegation to `auth` and `sync` which are already tested. Manual verification via the MCP client is the acceptance test.

- [ ] **Step 1: Add the personal pending flow global**

In `server.py`, after the existing `_pending_device_flow: dict = {}` line, add:

```python
_pending_device_flow_personal: dict = {}
```

- [ ] **Step 2: Add start_graph_auth_personal tool**

Add after the `complete_graph_auth` function:

```python
@mcp.tool()
def start_graph_auth_personal() -> dict:
    """
    Begin Microsoft Graph authorization for the personal account (pmoon@live.com) via device code flow.
    Returns a URL and a short code for the user to enter at that URL.
    After the user completes authorization in their browser, call complete_graph_auth_personal().
    """
    import requests
    global _pending_device_flow_personal
    r = requests.post(
        "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode",
        data={
            "client_id": _config["graph_client_id"],
            "scope": "Mail.Read User.Read offline_access",
        },
    )
    r.raise_for_status()
    flow = r.json()
    _pending_device_flow_personal = {
        "device_code": flow["device_code"],
        "interval": flow.get("interval", 5),
        "expires_at": time.time() + flow.get("expires_in", 900),
    }
    return {
        "action": "Visit the URL below and enter the code to authorize personal Outlook access (pmoon@live.com).",
        "url": flow["verification_uri"],
        "code": flow["user_code"],
        "next_step": "After completing authorization in your browser, call complete_graph_auth_personal().",
    }


@mcp.tool()
def complete_graph_auth_personal() -> dict:
    """
    Complete personal Microsoft Graph authorization after the user has entered the device code.
    Call this after start_graph_auth_personal() once you've authorized in the browser.
    """
    import requests
    global _pending_device_flow_personal
    if not _pending_device_flow_personal:
        return {"error": "No authorization in progress. Call start_graph_auth_personal() first."}
    if time.time() > _pending_device_flow_personal["expires_at"]:
        _pending_device_flow_personal = {}
        return {"error": "Authorization code expired. Call start_graph_auth_personal() to restart."}

    poll = requests.post(
        "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
        data={
            "client_id": _config["graph_client_id"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": _pending_device_flow_personal["device_code"],
        },
    )
    data = poll.json()
    if "access_token" in data:
        _config["graph_access_token_personal"] = data["access_token"]
        _config["graph_refresh_token_personal"] = data.get("refresh_token", "")
        _config["graph_token_expiry_personal"] = time.time() + data.get("expires_in", 3600)
        auth.save_config(_config)
        _pending_device_flow_personal = {}
        return {"status": "authorized", "message": "Personal Outlook access granted. You can now call sync_emails()."}
    error = data.get("error", "")
    if error == "authorization_pending":
        return {"status": "pending", "message": "Not yet authorized. Complete the steps in your browser, then call complete_graph_auth_personal() again."}
    _pending_device_flow_personal = {}
    return {"error": f"Authorization failed: {data.get('error_description', error)}"}
```

- [ ] **Step 3: Update sync_emails to run both accounts**

Replace the existing `sync_emails` function body:

```python
@mcp.tool()
def sync_emails() -> dict:
    """
    Incrementally sync Outlook emails since the last sync. Runs both work (pmoon@navicet.com)
    and personal (pmoon@live.com) accounts if personal is authorized.
    Matched emails (sender/recipient in CRM) go to the emails table.
    Unmatched emails go to unmatched_emails for contact discovery.
    Requires Graph authorization — call start_graph_auth() first if needed.
    For personal account, call start_graph_auth_personal() first.
    """
    result = sync_module.sync_graph(_config, conn=_conn)
    if _config.get("graph_refresh_token_personal"):
        personal = sync_module.sync_graph_personal(_config, conn=_conn)
        result["emails_matched_personal"] = personal["emails_matched"]
        result["emails_unmatched_personal"] = personal["emails_unmatched"]
    return result
```

- [ ] **Step 4: Run full test suite to confirm nothing is broken**

```
cd opcrm-mcp
pytest tests/ -v
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add opcrm-mcp/server.py
git commit -m "feat: add personal Graph auth tools and run both accounts in sync_emails"
```

---

## Task 5: First-time setup verification

This task is manual — no code to write. Confirm the end-to-end flow works before shipping.

- [ ] **Step 1: Confirm Azure app registration supports personal accounts**

In Azure Portal → App Registrations → your app (`d947a59a-...`) → Authentication:
- "Supported account types" must be **"Accounts in any organizational directory and personal Microsoft accounts"**
- If it currently says "Single tenant", change it and save

- [ ] **Step 2: Run start_graph_auth_personal via MCP**

In your MCP client (Claude Desktop), call `start_graph_auth_personal`. You should receive a URL and code. Open the URL, enter the code, and sign in as pmoon@live.com.

- [ ] **Step 3: Call complete_graph_auth_personal**

Call `complete_graph_auth_personal`. Expected response: `{"status": "authorized", "message": "Personal Outlook access granted..."}`.

Verify `opcrm-mcp/config.json` now has `graph_access_token_personal`, `graph_refresh_token_personal`, and `graph_token_expiry_personal`.

- [ ] **Step 4: Run sync_emails**

Call `sync_emails`. Expected response includes both work and personal counts:

```json
{
  "emails_matched": 12,
  "emails_unmatched": 45,
  "emails_matched_personal": 3,
  "emails_unmatched_personal": 28
}
```

- [ ] **Step 5: Verify cross-channel contact history**

If Jorge (jorge@designsnw.com) is in your CRM, call `get_contact_history` for his contact. Confirm emails from pmoon@live.com appear alongside work account emails, with `mailbox: "personal"` on the personal ones.

- [ ] **Step 6: Final commit and push**

```bash
git push
```
