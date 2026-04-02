import json
import sqlite3
import sys
import os
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db
import sync

CONFIG = {
    "opcrm_user_id": "uid", "opcrm_api_key": "key",
    "opcrm_my_user_id": "uid",
    "graph_client_id": "c", "graph_tenant_id": "t",
    "graph_access_token": "tok", "graph_refresh_token": "ref",
    "graph_token_expiry": 9999999999
}

RAW_CONTACT = {
    "contact": {
        "id": "c1", "first_name": "Alice", "last_name": "Smith",
        "emails": [{"value": "alice@acme.com"}], "company_name": "Acme Corp",
        "tags": ["investor"], "owner_id": "uid", "status": "active",
        "custom_fields": [
            {"custom_field": {"id": "699669362480755968b7997e"}, "value": "3"}
        ]
    }
}


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    return conn


def test_sync_opcrm_inserts_contact():
    conn = make_db()
    with patch("opcrm.fetch_all_contacts", return_value=[RAW_CONTACT]), \
         patch("opcrm.fetch_next_actions", return_value=[]), \
         patch("opcrm.fetch_all_pipelines", return_value=[]), \
         patch("opcrm.fetch_all_deals", return_value=[]):
        result = sync.sync_opcrm(CONFIG, conn=conn)

    conn.commit()
    row = conn.execute("SELECT * FROM contacts WHERE id = 'c1'").fetchone()
    assert row["name"] == "Alice Smith"
    assert row["cadence_months"] == 3
    assert row["email"] == "alice@acme.com"
    assert result["contacts_synced"] == 1


def test_sync_opcrm_inserts_tags():
    conn = make_db()
    with patch("opcrm.fetch_all_contacts", return_value=[RAW_CONTACT]), \
         patch("opcrm.fetch_next_actions", return_value=[]), \
         patch("opcrm.fetch_all_pipelines", return_value=[]), \
         patch("opcrm.fetch_all_deals", return_value=[]):
        sync.sync_opcrm(CONFIG, conn=conn)

    conn.commit()
    tags = {row[0] for row in conn.execute(
        "SELECT tag FROM contact_tags WHERE contact_id = 'c1'"
    ).fetchall()}
    assert tags == {"investor"}


def test_sync_graph_matches_email_to_contact():
    conn = make_db()
    db.upsert_contact(conn, {
        "id": "c1", "name": "Alice Smith", "company": "Acme",
        "email": "alice@acme.com", "phone": "", "owner_id": "uid",
        "status": "active", "cadence_months": 6, "raw_json": "{}"
    })
    conn.commit()

    email_msg = {
        "id": "msg1", "subject": "Checking in", "bodyPreview": "Hi Peter",
        "receivedDateTime": "2026-03-01T10:00:00Z", "isDraft": False,
        "threadId": "t1",
        "from": {"emailAddress": {"address": "alice@acme.com"}},
        "toRecipients": [{"emailAddress": {"address": "pmoon@navicet.com"}}],
    }

    with patch("graph.fetch_emails", return_value=[email_msg]), \
         patch("auth.get_graph_token", return_value="tok"):
        result = sync.sync_graph(CONFIG, conn=conn)

    conn.commit()
    row = conn.execute("SELECT * FROM emails WHERE id = 'msg1'").fetchone()
    assert row is not None
    assert row["contact_id"] == "c1"
    assert row["direction"] == "in"
    assert result["emails_matched"] == 1


def test_sync_graph_discards_unmatched_email():
    conn = make_db()
    conn.commit()

    email_msg = {
        "id": "msg_unknown", "subject": "Unknown", "bodyPreview": "...",
        "receivedDateTime": "2026-03-01T10:00:00Z", "isDraft": False,
        "threadId": "t1",
        "from": {"emailAddress": {"address": "stranger@nowhere.com"}},
        "toRecipients": [{"emailAddress": {"address": "pmoon@navicet.com"}}],
    }

    with patch("graph.fetch_emails", return_value=[email_msg]), \
         patch("auth.get_graph_token", return_value="tok"):
        sync.sync_graph(CONFIG, conn=conn)

    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    assert count == 0
