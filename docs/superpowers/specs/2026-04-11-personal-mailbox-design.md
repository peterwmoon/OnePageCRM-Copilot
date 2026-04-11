# Personal Mailbox Integration — Design Spec

**Date:** 2026-04-11
**Status:** Approved

---

## Overview

Add pmoon@live.com as a second synced mailbox alongside the existing pmoon@navicet.com work account. The personal account is used for concrete project work (Cascade Ridge), trust management, and personal business — with some bleed-through into CRM relationships (e.g., Jorge at Designs Northwest communicates with Peter primarily via his personal address).

**Goals:**
- Surface actionable emails from the personal inbox via `get_actionable_emails`, with a `mailbox` field identifying the source
- Match personal-account emails against CRM contacts so cross-channel contact history is unified (e.g., Jorge's emails via pmoon@live.com appear in his CRM history)
- No calendar sync for the personal account — pmoon@live.com calendar is not used

**Non-goals:**
- Generic multi-account abstraction (two accounts is the ceiling)
- Gmail or other providers

---

## Architecture

### 1. Config (`config.json`)

Three new flat keys added — same shape as the existing work account tokens:

```json
"graph_access_token_personal": "...",
"graph_refresh_token_personal": "...",
"graph_token_expiry_personal": 1234567890.0
```

No other config structure changes. Personal tokens are optional — the system degrades gracefully when absent.

### 2. Auth (`auth.py`)

`_refresh_token` and `_run_device_flow` are refactored to accept an `endpoint` parameter (the base OAuth URL). The work account continues passing its existing tenant URL — zero behavior change.

A new `get_graph_token_personal(config, config_path=None)` function mirrors `get_graph_token()`, using the `consumers` endpoint (`https://login.microsoftonline.com/consumers/oauth2/v2.0/...`) and reading/writing the `_personal` suffixed token keys.

### 3. Database (`db.py`)

`mailbox TEXT DEFAULT 'work'` added to both `emails` and `unmatched_emails` tables.

Migration: `init_db` runs `ALTER TABLE` for each column, wrapped in `try/except` to handle the case where the column already exists (SQLite does not support `ADD COLUMN IF NOT EXISTS`). Existing rows default to `'work'` — no backfill required.

Updated functions:
- `upsert_email` — accepts `mailbox` field
- `upsert_unmatched_email` — accepts `mailbox` field
- `get_actionable_emails` — adds `mailbox` to SELECT (no signature change)

### 4. Sync (`sync.py`)

`_process_email_batch(msgs, email_map, conn, mailbox='work')` — gains a `mailbox` parameter passed through to both upsert calls.

New `sync_graph_personal(config, conn=None, since_date=None)` function:
- Mirrors `sync_graph()` exactly
- Uses `auth.get_graph_token_personal(config)`
- Logs to source `'graph_personal'` in `sync_log`
- Syncs same folders: `me/messages` + `me/mailFolders/sentItems/messages`
- Passes `mailbox='personal'` to `_process_email_batch`

`sync_graph()` is untouched.

### 5. MCP Tools (`server.py`)

**New globals:**
- `_pending_device_flow_personal: dict = {}` — separate from the work account pending flow

**New tools:**
- `start_graph_auth_personal()` — initiates device flow against the `consumers` endpoint
- `complete_graph_auth_personal()` — polls for token completion, saves `_personal` keys

**Updated tool:**
- `sync_emails()` — calls `sync_graph()` as before, then calls `sync_graph_personal()` if `graph_refresh_token_personal` is present in config. Returns merged result:
  ```json
  {
    "emails_matched": 12,
    "emails_unmatched": 4,
    "emails_matched_personal": 3,
    "emails_unmatched_personal": 7
  }
  ```

**Unchanged tools:** `get_actionable_emails` automatically surfaces `mailbox` via the DB query — no signature change needed.

---

## Data Flow

```
pmoon@live.com inbox
  → graph.fetch_emails(personal_token)
  → _process_email_batch(..., mailbox='personal')
      ├─ matched CRM contact → emails table (mailbox='personal')
      └─ no CRM match       → unmatched_emails (mailbox='personal')

get_actionable_emails → returns both work + personal unmatched, with mailbox field
get_contact_history   → queries emails by contact_id, spans both mailboxes naturally
```

---

## Setup Sequence (one-time)

1. Update Azure App Registration to support personal Microsoft accounts (portal change — user)
2. Call `start_graph_auth_personal()` — get device code
3. Authenticate pmoon@live.com at the returned URL
4. Call `complete_graph_auth_personal()` — tokens saved to config.json
5. Call `sync_emails()` — personal account included automatically going forward

---

## Testing

- Unit tests for `get_graph_token_personal` (token fresh / expired / needs device flow)
- Unit tests for `_process_email_batch` with `mailbox` parameter
- Unit test for DB migration (column added to existing DB, existing rows default to `'work'`)
- Integration test: `sync_emails` merges results from both accounts when personal tokens present; skips personal gracefully when absent
