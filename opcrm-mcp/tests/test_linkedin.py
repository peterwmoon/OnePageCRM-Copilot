"""Tests for LinkedIn integration."""
import pytest
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import db
import linkedin as li


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
    csv_text = """First Name,Last Name,URL,Email Address,Company,Position,Connected On
Jane,Smith,,jane@example.com,Acme Corp,VP Sales,01 Jan 2023
"""
    result = li.parse_connections_csv(csv_text)
    assert len(result) == 0


def test_parse_connections_csv_raises_if_no_header():
    with pytest.raises(ValueError, match="header"):
        li.parse_connections_csv("This file has no CSV header at all.\n")


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
