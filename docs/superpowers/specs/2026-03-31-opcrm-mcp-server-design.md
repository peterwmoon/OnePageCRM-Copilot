# OnePageCRM MCP Server — Design Spec
**Date:** 2026-03-31
**Owner:** Peter Moon — pmoon@navicet.com

---

## Purpose

A local Python MCP server that gives Claude direct, read-write access to Peter's CRM data — contacts, full interaction history (notes, calls, logged meetings, and Outlook emails), cadence fields, tags, and next actions. Enables Claude to analyze relationship health across all contacts, identify who needs outreach, cluster contacts for batch planning, and draft or log communications.

---

## Architecture

Single Python package (`opcrm-mcp/`) alongside `index.html` in the repo. Runs as a local MCP server process connected to Claude via stdio. SQLite is the source of truth for all Claude queries — OPCRM and Microsoft Graph are only touched during sync.

```
OnePageCRM API  ──┐
                  ├──► sync.py ──► crm_cache.db (SQLite) ──► server.py ──► Claude
Microsoft Graph ──┘                                           (MCP tools)
```

**Files:**
```
opcrm-mcp/
  server.py         # MCP server — all tools Claude calls
  sync.py           # pulls OPCRM + Graph into SQLite
  auth.py           # credential storage + Graph OAuth device flow
  config.json       # OPCRM user ID, API key, Graph client ID (gitignored)
  crm_cache.db      # SQLite cache (gitignored)
  requirements.txt
```

**Startup:** `python server.py` — Claude connects via its MCP config. Sync is triggered via the `sync` tool or `python sync.py` directly.

---

## Database Schema

```sql
contacts (
  id TEXT PRIMARY KEY,
  name TEXT,
  company TEXT,
  email TEXT,
  phone TEXT,
  owner_id TEXT,
  status TEXT,
  cadence_days INTEGER,       -- from custom field 699669362480755968b7997e, default 180
  last_synced TEXT,
  raw_json TEXT               -- full OPCRM contact payload
)

contact_tags (
  contact_id TEXT,
  tag TEXT,
  PRIMARY KEY (contact_id, tag)
)

next_actions (
  id TEXT PRIMARY KEY,
  contact_id TEXT,
  text TEXT,
  due_date TEXT,
  assignee_id TEXT,
  status TEXT
)

notes (
  id TEXT PRIMARY KEY,
  contact_id TEXT,
  text TEXT,
  date TEXT,
  author_id TEXT
)

calls (
  id TEXT PRIMARY KEY,
  contact_id TEXT,
  text TEXT,
  date TEXT,
  author_id TEXT,
  duration INTEGER
)

meetings (
  id TEXT PRIMARY KEY,
  contact_id TEXT,            -- OPCRM-logged meetings only (not Outlook calendar)
  text TEXT,
  date TEXT,
  author_id TEXT
)

emails (
  id TEXT PRIMARY KEY,
  contact_id TEXT,
  subject TEXT,
  body_preview TEXT,
  date TEXT,
  direction TEXT,             -- 'in' or 'out'
  thread_id TEXT,
  from_address TEXT,
  to_addresses TEXT           -- JSON array
)

sync_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT,                -- 'opcrm' or 'graph'
  last_synced_at TEXT,
  contacts_synced INTEGER,
  records_synced INTEGER,
  error TEXT
)
```

**Notes:**
- `cadence_days` resolved from OPCRM custom field `699669362480755968b7997e`; falls back to 180 (6 months) if absent — matches existing app logic.
- `contact_tags` is a separate relational table (not a JSON column) to support clean `WHERE tag = X` queries for clustering.
- `raw_json` on contacts preserves all OPCRM fields not worth individual columns.
- Outlook calendar meetings are deliberately excluded — meeting invitations/accepts don't carry interaction intelligence.

---

## Sync Process

Sync runs in two phases. OPCRM runs first because Graph email matching depends on the contact email index it builds.

### Phase 1 — OPCRM

1. Fetch all contacts (paginated, 100/page) — upsert into `contacts`, replace `contact_tags`
2. Fetch all next actions — upsert into `next_actions`
3. For each contact, fetch notes, calls, and logged meetings — upsert into respective tables
4. Write `sync_log` record on completion or error

**Incremental sync:** uses `modified_since` parameter on subsequent syncs based on `last_synced_at` from `sync_log`. First sync is a full pull.

### Phase 2 — Microsoft Graph

1. Load Graph token from `config.json`; trigger device-code OAuth flow if absent or expired
2. Build `email_address → contact_id` map from `contacts` table
3. Fetch inbox + sent items (last 90 days, or since `last_synced_at` for incremental)
4. For each email, match sender/recipients against the contact map — discard if no match
5. Upsert matched emails into `emails` table with resolved `contact_id`

**Scope:** only emails where at least one party is a known OPCRM contact are stored. Unmatched emails are discarded — keeps the database lean.

---

## Authentication

### OnePageCRM
- Basic Auth: User ID + API Key
- Stored in `config.json` (gitignored)
- Entered once at setup, never re-prompted unless credentials change

### Microsoft Graph
- OAuth 2.0 device-code flow for initial authorization (user visits URL, enters code)
- Access token + refresh token persisted to `config.json`
- `auth.py` handles token refresh automatically before Graph calls
- Scopes required: `Mail.Read`, `User.Read`

---

## MCP Tools

### Sync

| Tool | Description |
|---|---|
| `sync()` | Full sync — OPCRM then Graph. Returns summary: contacts, records synced, errors. |
| `sync_status()` | Returns last sync time and record counts per table. No API calls. |

### Read — Contacts

| Tool | Description |
|---|---|
| `get_contact(id_or_name)` | Full profile: details, cadence, tags, owner, open next action |
| `search_contacts(query)` | Name/company/email search against SQLite |
| `list_contacts_by_tag(tag)` | All contacts with a given tag |
| `list_overdue_contacts()` | Contacts where last touch (across all channels) exceeds cadence_days |
| `list_contacts_by_owner(owner)` | Filter by assigned user |

### Read — History

| Tool | Description |
|---|---|
| `get_contact_history(contact_id, limit)` | Unified timeline of notes, calls, emails sorted by date |
| `get_recent_emails(contact_id, limit)` | Emails only, with body preview |
| `get_notes(contact_id)` | OPCRM notes only |

### Read — Analysis

| Tool | Description |
|---|---|
| `summarize_relationship(contact_id)` | Last touch date, channel, cadence status, open next action, recent topic keywords |
| `cluster_contacts(by)` | Group contacts by `tag`, `company`, or `cadence_status` for batch outreach planning |

### Write (all prompt before executing — see Write Rules)

| Tool | Description |
|---|---|
| `log_note(contact_id, text, confirmed)` | Create note in OPCRM; updates local cache |
| `create_next_action(contact_id, text, due_date, confirmed)` | Create next action; extra confirmation if contact not owned by you |
| `complete_next_action(action_id, confirmed)` | Mark action complete in OPCRM |
| `reschedule_next_action(action_id, new_date, confirmed)` | Update due date |
| `update_cadence(contact_id, days, confirmed)` | Update cadence custom field; highest confirmation bar |
| `add_tag(contact_id, tag, confirmed)` | Add tag — blocked if contact not owned by you |
| `remove_tag(contact_id, tag, confirmed)` | Remove tag — blocked if contact not owned by you |

---

## Write Rules

### Two-phase pattern
All write tools accept a `confirmed` boolean parameter (default `false`):
- `confirmed=false` — returns a preview of the change, no write occurs
- `confirmed=true` — executes write against OPCRM API, then updates SQLite

Claude physically cannot write without a second tool call with `confirmed=true`. The design forces a confirmation step into the flow rather than relying on Claude's judgment alone.

### Ownership rules
The server checks `contact.owner_id` against the configured user ID before any write:

| Operation | If owned by you | If owned by someone else |
|---|---|---|
| Log note | Prompt → confirm | Prompt → confirm |
| Create next action | Prompt → confirm | Extra confirmation step |
| Complete/reschedule action | Prompt → confirm | Extra confirmation step |
| Update cadence | Preview + warning → confirm | Blocked |
| Add/remove tag | Prompt → confirm | Blocked (error returned) |

### Cadence updates
`update_cadence` always previews first regardless of `confirmed` value, and includes a warning showing the current value and downstream effect on overdue detection in the existing CRM Co-Pilot app.

---

## Configuration

`config.json` (gitignored):
```json
{
  "opcrm_user_id": "...",
  "opcrm_api_key": "...",
  "opcrm_my_user_id": "...",
  "graph_client_id": "...",
  "graph_tenant_id": "...",
  "graph_access_token": "...",
  "graph_refresh_token": "...",
  "graph_token_expiry": 0
}
```

---

## Constraints

- Local only — no cloud hosting, no backend server, no external data routing
- Read-write to OPCRM; read-only from Graph (emails are pulled, not sent via this server)
- No scheduled/background sync — sync is always manually triggered
- SQLite file and config are gitignored — credentials never committed
