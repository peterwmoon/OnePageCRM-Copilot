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
