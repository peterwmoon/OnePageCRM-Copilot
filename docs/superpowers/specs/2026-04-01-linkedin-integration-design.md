# LinkedIn Integration — Design Spec

**Date:** 2026-04-01  
**Status:** Approved  
**Goal:** Detect when 1st-degree LinkedIn connections change jobs and automatically create timed follow-up actions in OnePageCRM.

---

## Background

Job changes are a high-value outreach trigger. The optimal contact window is approximately 3 months after someone starts a new role. This integration detects job changes by comparing LinkedIn connection exports over time and surfaces them via Claude MCP tools.

---

## Architecture

LinkedIn is a third data source alongside OnePageCRM and Microsoft Graph. Data flows in via a manual import script (run after downloading a LinkedIn data export), stored in SQLite alongside the existing cache, and surfaced through two new MCP tools.

No LinkedIn API is used. LinkedIn's official API does not grant messaging or connections access to personal apps. The data export approach (Settings → Data Privacy → Get a copy of your data → Connections) is reliable, ToS-compliant, and sufficient for a quarterly cadence.

---

## Data Model

Two new tables added to `crm_cache.db`.

### `linkedin_connections`

One row per connection per import snapshot.

| Column | Type | Notes |
|---|---|---|
| `linkedin_url` | TEXT | Stable unique identifier |
| `snapshot_date` | TEXT | YYYY-MM-DD, date import was run |
| `first_name` | TEXT | |
| `last_name` | TEXT | |
| `email` | TEXT | Only present if connection shared it |
| `company` | TEXT | |
| `position` | TEXT | |
| `connected_on` | TEXT | Date of LinkedIn connection |

Primary key: `(linkedin_url, snapshot_date)`  
Insert is `INSERT OR IGNORE` — re-running the same day is safe.

### `linkedin_import_log`

One row per import run.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | Autoincrement |
| `imported_at` | TEXT | datetime('now') |
| `connection_count` | INTEGER | Rows imported |

---

## Import Script — `linkedin_import.py`

**Usage:**
```
python linkedin_import.py Connections.csv
```

Accepts either the extracted `Connections.csv` or the LinkedIn export zip directly.

**Steps:**
1. Parse CSV — LinkedIn's format has a 3-line preamble before column headers; skip those rows
2. Insert all connections with today's date as `snapshot_date` using `INSERT OR IGNORE`
3. Log the import run to `linkedin_import_log`
4. Print summary: connections imported, duplicates skipped, whether a previous snapshot exists for comparison

---

## MCP Tools

### `detect_job_changes()`

Compares the two most recent distinct `snapshot_date` values. For each `linkedin_url` present in both snapshots where `company` or `position` changed, returns:

```json
{
  "changes": [
    {
      "name": "Jane Smith",
      "linkedin_url": "https://linkedin.com/in/janesmith",
      "email": "jane@example.com",
      "old_company": "Acme Corp",
      "old_position": "VP Sales",
      "new_company": "Globex",
      "new_position": "Chief Revenue Officer",
      "detected_date": "2026-04-01",
      "suggested_outreach_date": "2026-07-01",
      "contact_id": "abc123"
    }
  ],
  "not_in_crm": [
    {
      "name": "Bob Jones",
      "email": "",
      "old_company": "...",
      "new_company": "..."
    }
  ]
}
```

- `contact_id` is populated by matching `email` against `contacts.email` (case-insensitive)
- Connections without email, or whose email has no CRM match, go to `not_in_crm`
- If fewer than two snapshots exist: returns `{"message": "Only one snapshot exists — import again in ~3 months to detect changes."}`

### `create_job_change_action(contact_id, detected_date, confirmed=False)`

Creates a next action in OnePageCRM for a contact who changed jobs.

- Due date: `detected_date` + 90 days
- Action text: `"Job change follow-up — [Name] moved to [New Company]"`
- Follows the same `confirmed=False` preview / `confirmed=True` execute pattern as all other write tools
- Looks up the contact's current name and new company from the latest LinkedIn snapshot

---

## Edge Cases

| Scenario | Handling |
|---|---|
| Connection has no email | Goes to `not_in_crm`; no fuzzy name matching |
| First import only | `detect_job_changes()` returns informational message |
| Re-running import same day | `INSERT OR IGNORE` — safe, no duplicates |
| Same email matches multiple CRM contacts | All matches returned; user selects |
| Connection removed between snapshots | Silently ignored — not treated as a job change |
| Only company changed, title same (or vice versa) | Counts as a change |

---

## Workflow

1. Download LinkedIn data export (Settings → Data Privacy → Get a copy of your data → Connections)
2. Run `python linkedin_import.py Connections.csv`
3. Ask Claude: *"Who changed jobs since my last LinkedIn import?"*
4. Claude calls `detect_job_changes()` and presents the list
5. Ask Claude: *"Create next actions for the ones in my CRM"*
6. Claude calls `create_job_change_action()` for each, with preview → confirm

---

## Out of Scope

- LinkedIn messages (low volume, lower value than job change detection)
- Real-time sync or LinkedIn API integration
- Fuzzy name matching for unmatched connections
- Enriching existing CRM contacts with LinkedIn profile URLs
