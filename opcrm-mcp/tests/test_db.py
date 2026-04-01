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
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
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
