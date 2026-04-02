"""Tests for LinkedIn integration."""
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
    csv = """First Name,Last Name,URL,Email Address,Company,Position,Connected On
Jane,Smith,,jane@example.com,Acme Corp,VP Sales,01 Jan 2023
"""
    result = li.parse_connections_csv(csv)
    assert len(result) == 0


def test_parse_connections_csv_raises_if_no_header():
    import pytest
    with pytest.raises(ValueError, match="header"):
        li.parse_connections_csv("This file has no CSV header at all.\n")
