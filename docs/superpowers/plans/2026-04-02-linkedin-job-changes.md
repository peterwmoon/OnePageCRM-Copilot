# LinkedIn Job Change Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect when 1st-degree LinkedIn connections change jobs and surface them with 90-day follow-up actions in OnePageCRM.

**Architecture:** A manual import script loads LinkedIn's Connections.csv export into a snapshot table in `crm_cache.db`. Two new MCP tools query that table — one to diff snapshots and surface job changes, one to create follow-up next actions. CRM matching is by email address only.

**Tech Stack:** Python 3.12, SQLite (via existing `db.py`), pytest, existing `write_helpers.create_next_action` for CRM writes.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `opcrm-mcp/db.py` | Modify | Add LinkedIn schema tables and two insert helpers |
| `opcrm-mcp/linkedin.py` | Create | CSV parsing and job-change diff logic |
| `opcrm-mcp/linkedin_import.py` | Create | CLI script: read zip/CSV, load snapshot, print summary |
| `opcrm-mcp/server.py` | Modify | Add `detect_job_changes` and `create_job_change_action` MCP tools |
| `opcrm-mcp/tests/test_linkedin.py` | Create | Tests for schema, CSV parsing, and change detection |

---

## Task 1: Add LinkedIn Schema to `db.py`

**Files:**
- Modify: `opcrm-mcp/db.py`
- Create: `opcrm-mcp/tests/test_linkedin.py`

- [ ] **Step 1: Create the tests directory and write the failing schema test**

Create `opcrm-mcp/tests/__init__.py` (empty file) and `opcrm-mcp/tests/test_linkedin.py`:

```python
"""Tests for LinkedIn integration."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import db


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ── Schema tests ──────────────────────────────────────────────────────────────

def test_linkedin_tables_created():
    conn = make_conn()
    db.init_db(conn)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "linkedin_connections" in tables
    assert "linkedin_import_log" in tables
```

- [ ] **Step 2: Run test to confirm it fails**

```
cd opcrm-mcp
python -m pytest tests/test_linkedin.py::test_linkedin_tables_created -v
```

Expected: FAIL — `AssertionError: assert 'linkedin_connections' in ...`

- [ ] **Step 3: Add the LinkedIn schema to `db.py`**

In `db.py`, find the `SCHEMA` string. Append these tables before the closing `"""`:

```python
CREATE TABLE IF NOT EXISTS linkedin_connections (
    linkedin_url TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    email TEXT,
    company TEXT,
    position TEXT,
    connected_on TEXT,
    PRIMARY KEY (linkedin_url, snapshot_date)
);

CREATE TABLE IF NOT EXISTS linkedin_import_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imported_at TEXT DEFAULT (datetime('now')),
    snapshot_date TEXT,
    connection_count INTEGER DEFAULT 0
);
```

- [ ] **Step 4: Run test to confirm it passes**

```
python -m pytest tests/test_linkedin.py::test_linkedin_tables_created -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add opcrm-mcp/db.py opcrm-mcp/tests/__init__.py opcrm-mcp/tests/test_linkedin.py
git commit -m "feat: add LinkedIn snapshot schema to db"
```

---

## Task 2: Add DB Helper Functions to `db.py`

**Files:**
- Modify: `opcrm-mcp/db.py`
- Modify: `opcrm-mcp/tests/test_linkedin.py`

- [ ] **Step 1: Write failing tests for the two db helpers**

Append to `tests/test_linkedin.py`:

```python
# ── DB helper tests ───────────────────────────────────────────────────────────

def test_insert_linkedin_connection_inserts_row():
    conn = make_conn()
    db.init_db(conn)
    c = {
        "linkedin_url": "https://linkedin.com/in/janesmith",
        "first_name": "Jane",
        "last_name": "Smith",
        "email": "jane@example.com",
        "company": "Acme Corp",
        "position": "VP Sales",
        "connected_on": "01 Jan 2023",
    }
    db.insert_linkedin_connection(conn, c, "2026-01-01")
    rows = conn.execute("SELECT * FROM linkedin_connections").fetchall()
    assert len(rows) == 1
    assert rows[0]["company"] == "Acme Corp"


def test_insert_linkedin_connection_ignores_duplicate():
    conn = make_conn()
    db.init_db(conn)
    c = {
        "linkedin_url": "https://linkedin.com/in/janesmith",
        "first_name": "Jane",
        "last_name": "Smith",
        "email": "jane@example.com",
        "company": "Acme Corp",
        "position": "VP Sales",
        "connected_on": "01 Jan 2023",
    }
    db.insert_linkedin_connection(conn, c, "2026-01-01")
    db.insert_linkedin_connection(conn, c, "2026-01-01")
    rows = conn.execute("SELECT * FROM linkedin_connections").fetchall()
    assert len(rows) == 1


def test_log_linkedin_import_inserts_row():
    conn = make_conn()
    db.init_db(conn)
    db.log_linkedin_import(conn, "2026-01-01", 42)
    conn.commit()
    row = conn.execute("SELECT * FROM linkedin_import_log").fetchone()
    assert row["snapshot_date"] == "2026-01-01"
    assert row["connection_count"] == 42
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/test_linkedin.py::test_insert_linkedin_connection_inserts_row tests/test_linkedin.py::test_insert_linkedin_connection_ignores_duplicate tests/test_linkedin.py::test_log_linkedin_import_inserts_row -v
```

Expected: FAIL — `AttributeError: module 'db' has no attribute 'insert_linkedin_connection'`

- [ ] **Step 3: Add the two helper functions to `db.py`**

Add these at the bottom of `db.py`, after `upsert_calendar_event`:

```python
def insert_linkedin_connection(conn, c, snapshot_date):
    conn.execute("""
        INSERT OR IGNORE INTO linkedin_connections
            (linkedin_url, snapshot_date, first_name, last_name,
             email, company, position, connected_on)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        c["linkedin_url"], snapshot_date,
        c.get("first_name", ""), c.get("last_name", ""),
        c.get("email", ""), c.get("company", ""),
        c.get("position", ""), c.get("connected_on", ""),
    ))


def log_linkedin_import(conn, snapshot_date, connection_count):
    conn.execute("""
        INSERT INTO linkedin_import_log (snapshot_date, connection_count)
        VALUES (?, ?)
    """, (snapshot_date, connection_count))
```

- [ ] **Step 4: Run tests to confirm they pass**

```
python -m pytest tests/test_linkedin.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add opcrm-mcp/db.py opcrm-mcp/tests/test_linkedin.py
git commit -m "feat: add LinkedIn db helper functions"
```

---

## Task 3: Create `linkedin.py` — CSV Parsing

**Files:**
- Create: `opcrm-mcp/linkedin.py`
- Modify: `opcrm-mcp/tests/test_linkedin.py`

LinkedIn's `Connections.csv` has a variable number of preamble lines before the CSV header. The header row always starts with `First Name`. The file is UTF-8 with a BOM.

- [ ] **Step 1: Write failing CSV parsing tests**

Append to `tests/test_linkedin.py`:

```python
import linkedin as li

# ── CSV parsing tests ─────────────────────────────────────────────────────────

SAMPLE_CSV_CLEAN = """First Name,Last Name,URL,Email Address,Company,Position,Connected On
Jane,Smith,https://linkedin.com/in/janesmith,jane@example.com,Acme Corp,VP Sales,01 Jan 2023
Bob,Jones,https://linkedin.com/in/bobjones,,Globex,Engineer,15 Mar 2022
"""

SAMPLE_CSV_WITH_PREAMBLE = """Notes: To protect our members' privacy, we limit profile viewing.

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Jane,Smith,https://linkedin.com/in/janesmith,jane@example.com,Acme Corp,VP Sales,01 Jan 2023
"""


def test_parse_connections_csv_basic():
    result = li.parse_connections_csv(SAMPLE_CSV_CLEAN)
    assert len(result) == 2
    assert result[0]["linkedin_url"] == "https://linkedin.com/in/janesmith"
    assert result[0]["first_name"] == "Jane"
    assert result[0]["last_name"] == "Smith"
    assert result[0]["email"] == "jane@example.com"
    assert result[0]["company"] == "Acme Corp"
    assert result[0]["position"] == "VP Sales"
    assert result[0]["connected_on"] == "01 Jan 2023"


def test_parse_connections_csv_missing_email():
    result = li.parse_connections_csv(SAMPLE_CSV_CLEAN)
    bob = next(r for r in result if r["first_name"] == "Bob")
    assert bob["email"] == ""


def test_parse_connections_csv_skips_preamble():
    result = li.parse_connections_csv(SAMPLE_CSV_WITH_PREAMBLE)
    assert len(result) == 1
    assert result[0]["first_name"] == "Jane"


def test_parse_connections_csv_skips_rows_without_url():
    csv = """First Name,Last Name,URL,Email Address,Company,Position,Connected On
Jane,Smith,,jane@example.com,Acme Corp,VP Sales,01 Jan 2023
"""
    result = li.parse_connections_csv(csv)
    assert len(result) == 0


def test_parse_connections_csv_raises_if_no_header():
    import pytest
    with pytest.raises(ValueError, match="header"):
        li.parse_connections_csv("This file has no CSV header at all.\n")
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/test_linkedin.py::test_parse_connections_csv_basic tests/test_linkedin.py::test_parse_connections_csv_missing_email tests/test_linkedin.py::test_parse_connections_csv_skips_preamble tests/test_linkedin.py::test_parse_connections_csv_skips_rows_without_url tests/test_linkedin.py::test_parse_connections_csv_raises_if_no_header -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'linkedin'`

- [ ] **Step 3: Create `opcrm-mcp/linkedin.py` with the parser**

```python
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
```

- [ ] **Step 4: Run tests to confirm they pass**

```
python -m pytest tests/test_linkedin.py -v
```

Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add opcrm-mcp/linkedin.py opcrm-mcp/tests/test_linkedin.py
git commit -m "feat: LinkedIn CSV parser with preamble handling"
```

---

## Task 4: Add Job-Change Detection to `linkedin.py`

**Files:**
- Modify: `opcrm-mcp/linkedin.py`
- Modify: `opcrm-mcp/tests/test_linkedin.py`

- [ ] **Step 1: Write failing change-detection tests**

Append to `tests/test_linkedin.py`:

```python
# ── Change detection tests ────────────────────────────────────────────────────

def _seed_snapshot(conn, snapshot_date, rows):
    """Insert rows into linkedin_connections for a given snapshot date."""
    for r in rows:
        db.insert_linkedin_connection(conn, r, snapshot_date)
    conn.commit()


JANE = {
    "linkedin_url": "https://linkedin.com/in/janesmith",
    "first_name": "Jane", "last_name": "Smith",
    "email": "jane@example.com",
    "company": "Acme Corp", "position": "VP Sales",
    "connected_on": "01 Jan 2023",
}

JANE_NEW_JOB = {**JANE, "company": "Globex", "position": "Chief Revenue Officer"}

BOB = {
    "linkedin_url": "https://linkedin.com/in/bobjones",
    "first_name": "Bob", "last_name": "Jones",
    "email": "",  # no email
    "company": "Initech", "position": "Engineer",
    "connected_on": "15 Mar 2022",
}

BOB_NEW_JOB = {**BOB, "company": "Megacorp", "position": "Senior Engineer"}


def test_detect_job_changes_returns_none_with_one_snapshot():
    conn = make_conn()
    db.init_db(conn)
    _seed_snapshot(conn, "2026-01-01", [JANE])
    result = li.detect_job_changes(conn, {})
    assert result is None


def test_detect_job_changes_finds_changed_contact_in_crm():
    conn = make_conn()
    db.init_db(conn)
    _seed_snapshot(conn, "2026-01-01", [JANE])
    _seed_snapshot(conn, "2026-04-01", [JANE_NEW_JOB])
    email_map = {"jane@example.com": "contact-123"}

    result = li.detect_job_changes(conn, email_map)

    assert len(result["changes"]) == 1
    change = result["changes"][0]
    assert change["contact_id"] == "contact-123"
    assert change["old_company"] == "Acme Corp"
    assert change["new_company"] == "Globex"
    assert change["detected_date"] == "2026-04-01"
    assert change["suggested_outreach_date"] == "2026-06-30"
    assert len(result["not_in_crm"]) == 0


def test_detect_job_changes_no_email_goes_to_not_in_crm():
    conn = make_conn()
    db.init_db(conn)
    _seed_snapshot(conn, "2026-01-01", [BOB])
    _seed_snapshot(conn, "2026-04-01", [BOB_NEW_JOB])

    result = li.detect_job_changes(conn, {})

    assert len(result["changes"]) == 0
    assert len(result["not_in_crm"]) == 1
    assert result["not_in_crm"][0]["new_company"] == "Megacorp"


def test_detect_job_changes_no_change_excluded():
    conn = make_conn()
    db.init_db(conn)
    _seed_snapshot(conn, "2026-01-01", [JANE])
    _seed_snapshot(conn, "2026-04-01", [JANE])  # same data
    email_map = {"jane@example.com": "contact-123"}

    result = li.detect_job_changes(conn, email_map)

    assert len(result["changes"]) == 0
    assert len(result["not_in_crm"]) == 0


def test_detect_job_changes_new_connection_excluded():
    """Someone who only appears in the latest snapshot (new connection) is not a job change."""
    conn = make_conn()
    db.init_db(conn)
    _seed_snapshot(conn, "2026-01-01", [JANE])
    _seed_snapshot(conn, "2026-04-01", [JANE, BOB_NEW_JOB])

    result = li.detect_job_changes(conn, {"jane@example.com": "contact-123"})

    # Bob only appears in latest snapshot — not a job change
    assert all(c["linkedin_url"] != BOB["linkedin_url"] for c in result["changes"])
    assert all(c["linkedin_url"] != BOB["linkedin_url"] for c in result["not_in_crm"])


def test_detect_job_changes_unmatched_email_goes_to_not_in_crm():
    """Contact with email that doesn't match any CRM contact goes to not_in_crm."""
    conn = make_conn()
    db.init_db(conn)
    _seed_snapshot(conn, "2026-01-01", [JANE])
    _seed_snapshot(conn, "2026-04-01", [JANE_NEW_JOB])

    result = li.detect_job_changes(conn, {})  # empty email_map — no CRM contacts

    assert len(result["changes"]) == 0
    assert len(result["not_in_crm"]) == 1
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/test_linkedin.py::test_detect_job_changes_returns_none_with_one_snapshot tests/test_linkedin.py::test_detect_job_changes_finds_changed_contact_in_crm -v
```

Expected: FAIL — `AttributeError: module 'linkedin' has no attribute 'detect_job_changes'`

- [ ] **Step 3: Add `detect_job_changes` to `linkedin.py`**

Append to `opcrm-mcp/linkedin.py`:

```python
def detect_job_changes(conn, email_map):
    """
    Compare the two most recent LinkedIn snapshots and return job-change results.

    email_map: dict of {email_lower: contact_id} — used to match connections to CRM.

    Returns None if fewer than two snapshots exist.
    Returns dict:
        {
            "changes": [...],      # changed connections matched to CRM contacts
            "not_in_crm": [...],   # changed connections with no CRM match
        }
    Each item contains: name, linkedin_url, email, old_company, old_position,
    new_company, new_position, detected_date, suggested_outreach_date,
    and contact_id (changes list only).
    """
    dates = conn.execute("""
        SELECT DISTINCT snapshot_date FROM linkedin_connections
        ORDER BY snapshot_date DESC LIMIT 2
    """).fetchall()

    if len(dates) < 2:
        return None

    latest_date = dates[0]["snapshot_date"]
    prev_date = dates[1]["snapshot_date"]

    latest = {
        row["linkedin_url"]: dict(row)
        for row in conn.execute(
            "SELECT * FROM linkedin_connections WHERE snapshot_date = ?",
            (latest_date,),
        ).fetchall()
    }
    previous = {
        row["linkedin_url"]: dict(row)
        for row in conn.execute(
            "SELECT * FROM linkedin_connections WHERE snapshot_date = ?",
            (prev_date,),
        ).fetchall()
    }

    outreach_date = (
        date.fromisoformat(latest_date) + timedelta(days=90)
    ).isoformat()

    changes = []
    not_in_crm = []

    for url, current in latest.items():
        if url not in previous:
            continue  # new connection, not a job change
        prev = previous[url]
        if (current["company"] == prev["company"]
                and current["position"] == prev["position"]):
            continue  # no change

        entry = {
            "name": f"{current['first_name']} {current['last_name']}".strip(),
            "linkedin_url": url,
            "email": current.get("email", ""),
            "old_company": prev["company"],
            "old_position": prev["position"],
            "new_company": current["company"],
            "new_position": current["position"],
            "detected_date": latest_date,
            "suggested_outreach_date": outreach_date,
        }

        email = current.get("email", "")
        contact_id = email_map.get(email) if email else None

        if contact_id:
            entry["contact_id"] = contact_id
            changes.append(entry)
        else:
            not_in_crm.append(entry)

    return {"changes": changes, "not_in_crm": not_in_crm}
```

- [ ] **Step 4: Run all tests to confirm they pass**

```
python -m pytest tests/test_linkedin.py -v
```

Expected: all 15 tests PASS

- [ ] **Step 5: Commit**

```bash
git add opcrm-mcp/linkedin.py opcrm-mcp/tests/test_linkedin.py
git commit -m "feat: LinkedIn job-change detection"
```

---

## Task 5: Create `linkedin_import.py`

**Files:**
- Create: `opcrm-mcp/linkedin_import.py`

- [ ] **Step 1: Create the import script**

Create `opcrm-mcp/linkedin_import.py`:

```python
"""
Import LinkedIn connections from a data export CSV or zip.

Usage:
    python linkedin_import.py Connections.csv
    python linkedin_import.py linkedin_data_export.zip

How to get your LinkedIn export:
    LinkedIn → Settings → Data Privacy → Get a copy of your data → Connections
    Download the zip (usually ready within minutes).

Run this every ~3 months. After importing, ask Claude:
    "Who changed jobs since my last LinkedIn import?"
"""
import sys
import zipfile
from datetime import date
from pathlib import Path

import db
import linkedin as li


def main():
    if len(sys.argv) < 2:
        print("Usage: python linkedin_import.py <Connections.csv or export.zip>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"ERROR: File not found: {path}")
        sys.exit(1)

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            csv_name = next(
                (n for n in names if "Connections" in n and n.endswith(".csv")),
                None,
            )
            if not csv_name:
                print(f"ERROR: Could not find Connections.csv in zip.")
                print(f"Files in zip: {names}")
                sys.exit(1)
            text = zf.read(csv_name).decode("utf-8-sig")
    else:
        text = path.read_text(encoding="utf-8-sig")

    try:
        connections = li.parse_connections_csv(text)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    conn = db.get_conn()
    db.init_db(conn)

    snapshot_date = date.today().isoformat()

    # Check existing snapshots before inserting
    existing_dates = [
        r["snapshot_date"]
        for r in conn.execute(
            "SELECT DISTINCT snapshot_date FROM linkedin_connections "
            "ORDER BY snapshot_date DESC"
        ).fetchall()
    ]

    inserted = 0
    for c in connections:
        before = conn.total_changes
        db.insert_linkedin_connection(conn, c, snapshot_date)
        if conn.total_changes > before:
            inserted += 1

    skipped = len(connections) - inserted
    db.log_linkedin_import(conn, snapshot_date, inserted)
    conn.commit()
    conn.close()

    print(f"Snapshot date : {snapshot_date}")
    print(f"Connections   : {len(connections)} total, {inserted} new, {skipped} already existed")

    if not existing_dates:
        print()
        print("First import complete.")
        print('Run again in ~3 months, then ask Claude: "Who changed jobs since my last LinkedIn import?"')
    elif snapshot_date not in existing_dates:
        prev = existing_dates[0]
        print()
        print(f"Two snapshots available: {prev} and {snapshot_date}.")
        print('Ask Claude: "Who changed jobs since my last LinkedIn import?"')


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test with a real LinkedIn export (manual)**

Download your LinkedIn data export (Settings → Data Privacy → Get a copy of your data → Connections). Then run:

```
python linkedin_import.py <path-to-Connections.csv or export.zip>
```

Expected output (first run):
```
Snapshot date : 2026-04-02
Connections   : NNN total, NNN new, 0 already existed

First import complete.
Run again in ~3 months, then ask Claude: "Who changed jobs since my last LinkedIn import?"
```

Re-run immediately to verify the duplicate-skip logic:

```
python linkedin_import.py <same file>
```

Expected: `NNN total, 0 new, NNN already existed`

- [ ] **Step 3: Commit**

```bash
git add opcrm-mcp/linkedin_import.py
git commit -m "feat: LinkedIn import script with zip and CSV support"
```

---

## Task 6: Add MCP Tools to `server.py`

**Files:**
- Modify: `opcrm-mcp/server.py`

- [ ] **Step 1: Add the import at the top of `server.py`**

At the top of `server.py`, after the existing imports, add:

```python
import linkedin as li
```

- [ ] **Step 2: Add `detect_job_changes` tool to `server.py`**

Add after the `find_unknown_contacts` tool (around line 81):

```python
@mcp.tool()
def detect_job_changes() -> dict:
    """
    Compare the two most recent LinkedIn connection snapshots to find people who changed jobs.
    Returns two lists:
      - changes: people matched to CRM contacts, with suggested outreach dates
      - not_in_crm: changed connections not found in your CRM
    Run linkedin_import.py first to populate snapshots. Requires at least two imports.
    """
    email_map = {
        row["email"].lower(): row["id"]
        for row in _conn.execute(
            "SELECT id, email FROM contacts WHERE email != ''"
        ).fetchall()
    }
    result = li.detect_job_changes(_conn, email_map)
    if result is None:
        return {
            "message": (
                "Only one LinkedIn snapshot exists — run linkedin_import.py again "
                "in ~3 months to detect changes."
            )
        }
    return result
```

- [ ] **Step 3: Add `create_job_change_action` tool to `server.py`**

Add immediately after `detect_job_changes`:

```python
@mcp.tool()
def create_job_change_action(
    contact_id: str,
    detected_date: str,
    new_company: str,
    confirmed: bool = False,
) -> dict:
    """
    Create a follow-up next action for a contact who changed jobs.
    The action is due 90 days after detected_date.
    Call with confirmed=False first to preview, then confirmed=True to execute.

    contact_id:    CRM contact ID (from detect_job_changes output).
    detected_date: YYYY-MM-DD date the job change was detected (from detect_job_changes).
    new_company:   The contact's new company name (from detect_job_changes).
    """
    from datetime import date, timedelta

    contact = db.get_contact_by_id(_conn, contact_id)
    if contact is None:
        return {"error": f"Contact {contact_id} not found. Run sync first."}

    try:
        due_date = (
            date.fromisoformat(detected_date) + timedelta(days=90)
        ).isoformat()
    except ValueError:
        return {
            "error": f"Invalid detected_date '{detected_date}'. Use YYYY-MM-DD format."
        }

    text = f"Job change follow-up — {contact['name']} moved to {new_company}"
    return write_helpers.create_next_action(
        _config, _conn, contact_id, text, due_date, confirmed
    )
```

- [ ] **Step 4: Verify server starts without errors**

```
python -c "import server; print('OK')"
```

Expected: `OK` (no import errors)

- [ ] **Step 5: Commit**

```bash
git add opcrm-mcp/server.py
git commit -m "feat: detect_job_changes and create_job_change_action MCP tools"
```

---

## Task 7: Run Full Test Suite

- [ ] **Step 1: Run all tests**

```
python -m pytest tests/ -v
```

Expected: all 15 tests PASS, 0 failures

- [ ] **Step 2: Remove debug print from `auth.py`**

In `opcrm-mcp/auth.py`, remove the `print(r.json())` line added during debugging (line 69). The function should go straight from the `requests.post` call to `r.raise_for_status()`.

- [ ] **Step 3: Commit**

```bash
git add opcrm-mcp/auth.py
git commit -m "chore: remove debug print from auth token refresh"
```
