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


def test_get_actionable_emails_returns_emails_since_date():
    conn = make_conn()
    conn.execute("""
        INSERT INTO unmatched_emails (id, subject, body_preview, date, direction, from_address, to_addresses, conversation_id)
        VALUES
            ('e1', 'Invoice #100', 'Please pay...', '2026-03-28T10:00:00Z', 'in', 'billing@acme.com', '[]', 'c1'),
            ('e2', 'Old invoice', 'Old stuff',    '2026-03-01T10:00:00Z', 'in', 'billing@acme.com', '[]', 'c2'),
            ('e3', 'Meeting request', 'Let us meet', '2026-03-29T09:00:00Z', 'in', 'partner@firm.com', '[]', 'c3')
    """)
    conn.commit()
    results = db.get_actionable_emails(conn, since='2026-03-26')
    ids = [r['id'] for r in results]
    assert 'e1' in ids
    assert 'e3' in ids
    assert 'e2' not in ids  # before since date


def test_get_actionable_emails_filters_noise():
    conn = make_conn()
    conn.execute("""
        INSERT INTO unmatched_emails (id, subject, body_preview, date, direction, from_address, to_addresses, conversation_id)
        VALUES
            ('n1', 'Newsletter', 'Unsubscribe here', '2026-03-28T10:00:00Z', 'in', 'news@newsletter.com', '[]', 'c1'),
            ('n2', 'Alert!',     'System alert',     '2026-03-28T11:00:00Z', 'in', 'noreply@system.com', '[]', 'c2'),
            ('n3', 'Real email', 'Hey Peter',        '2026-03-28T12:00:00Z', 'in', 'alice@partner.com',  '[]', 'c3')
    """)
    conn.commit()
    results = db.get_actionable_emails(conn, since='2026-03-26')
    ids = [r['id'] for r in results]
    assert 'n3' in ids
    assert 'n1' not in ids
    assert 'n2' not in ids


def test_get_actionable_emails_sorted_most_recent_first():
    conn = make_conn()
    conn.execute("""
        INSERT INTO unmatched_emails (id, subject, body_preview, date, direction, from_address, to_addresses, conversation_id)
        VALUES
            ('d1', 'Earlier', 'body', '2026-03-27T08:00:00Z', 'in', 'alice@partner.com', '[]', 'c1'),
            ('d2', 'Later',   'body', '2026-03-29T08:00:00Z', 'in', 'bob@partner.com',   '[]', 'c2')
    """)
    conn.commit()
    results = db.get_actionable_emails(conn, since='2026-03-26')
    assert results[0]['id'] == 'd2'
    assert results[1]['id'] == 'd1'


def test_get_actionable_emails_returns_expected_fields():
    conn = make_conn()
    conn.execute("""
        INSERT INTO unmatched_emails (id, subject, body_preview, date, direction, from_address, to_addresses, conversation_id)
        VALUES ('f1', 'Invoice #200', 'Please remit', '2026-03-28T10:00:00Z', 'in', 'billing@vendor.com', '[]', 'c1')
    """)
    conn.commit()
    results = db.get_actionable_emails(conn, since='2026-03-26')
    assert len(results) == 1
    r = results[0]
    assert r['id'] == 'f1'
    assert r['subject'] == 'Invoice #200'
    assert r['body_preview'] == 'Please remit'
    assert r['date'] == '2026-03-28T10:00:00Z'
    assert r['from_address'] == 'billing@vendor.com'
    assert r['direction'] == 'in'


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
