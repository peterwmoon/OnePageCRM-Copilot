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
        "notes", "calls", "meetings", "emails", "sync_log",
        "unmatched_emails", "calendar_events", "calendar_event_contacts",
        "linkedin_connections", "linkedin_import_log",
        "deals", "pipelines", "pipeline_stages",
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


def test_get_sync_status_returns_most_recent_row():
    conn = make_conn()
    db.log_sync(conn, "opcrm", contacts_synced=5, records_synced=30)
    db.log_sync(conn, "opcrm", contacts_synced=12, records_synced=80)
    conn.commit()
    status = db.get_sync_status(conn)
    # Should return the most recent row's counts, not an arbitrary one
    assert status["opcrm"]["contacts_synced"] == 12


def test_get_all_tags_returns_distinct_tags():
    conn = make_conn()
    seed_contact(conn, contact_id="c1", tags=["investor", "board"])
    seed_contact(conn, contact_id="c2", email="b@b.com", tags=["investor"])
    conn.commit()
    tags = db.get_all_tags(conn)
    assert set(tags) == {"investor", "board"}
    assert len(tags) == 2  # no duplicates


def test_list_contacts_by_owner():
    conn = make_conn()
    seed_contact(conn, contact_id="c1", owner_id="u1")
    seed_contact(conn, contact_id="c2", email="b@b.com", owner_id="u2")
    conn.commit()
    results = db.list_contacts_by_owner(conn, "u1")
    assert len(results) == 1
    assert results[0]["id"] == "c1"


def test_get_recent_emails():
    conn = make_conn()
    seed_contact(conn)
    db.upsert_email(conn, {
        "id": "e1", "contact_id": "c1", "subject": "Hello",
        "body_preview": "Hi", "date": "2026-01-01",
        "direction": "in", "thread_id": "t1",
        "from_address": "alice@acme.com", "to_addresses": "[]"
    })
    conn.commit()
    emails = db.get_recent_emails(conn, "c1")
    assert len(emails) == 1
    assert emails[0]["subject"] == "Hello"


def test_get_notes_returns_notes():
    conn = make_conn()
    seed_contact(conn)
    db.upsert_note(conn, {
        "id": "n1", "contact_id": "c1", "text": "Met at conference",
        "date": "2026-01-01", "author_id": "u1"
    })
    conn.commit()
    notes = db.get_notes(conn, "c1")
    assert len(notes) == 1
    assert notes[0]["text"] == "Met at conference"
