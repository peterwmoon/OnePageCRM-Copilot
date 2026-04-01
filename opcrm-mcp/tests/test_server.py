import json
import sqlite3
import sys
import os
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db
import write_helpers

CONFIG = {
    "opcrm_user_id": "uid", "opcrm_api_key": "key",
    "opcrm_my_user_id": "uid",
    "graph_client_id": "c", "graph_tenant_id": "t",
    "graph_access_token": "", "graph_refresh_token": "", "graph_token_expiry": 0
}


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    return conn


def seed_contact(conn, contact_id="c1", owner_id="uid"):
    db.upsert_contact(conn, {
        "id": contact_id, "name": "Alice Smith", "company": "Acme",
        "email": "alice@acme.com", "phone": "", "owner_id": owner_id,
        "status": "active", "cadence_months": 6, "raw_json": "{}"
    })
    conn.commit()


# ── log_note ──────────────────────────────────────────────────────────────────

def test_log_note_preview_does_not_write():
    conn = make_conn()
    seed_contact(conn)
    result = write_helpers.log_note(CONFIG, conn, "c1", "Follow up", confirmed=False)
    assert result["confirmation_required"] is True
    assert "Follow up" in result["preview"]
    count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    assert count == 0


def test_log_note_confirmed_calls_api_and_updates_db():
    conn = make_conn()
    seed_contact(conn)
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": {"note": {"id": "n_new", "contact_id": "c1",
                          "text": "Follow up", "date": "2026-03-31", "author_id": "uid"}}
    }
    mock_response.raise_for_status = MagicMock()
    with patch("requests.post", return_value=mock_response):
        result = write_helpers.log_note(CONFIG, conn, "c1", "Follow up", confirmed=True)
    assert result["success"] is True
    count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    assert count == 1


# ── create_next_action ────────────────────────────────────────────────────────

def test_create_action_preview_does_not_write():
    conn = make_conn()
    seed_contact(conn)
    result = write_helpers.create_next_action(
        CONFIG, conn, "c1", "Send proposal", "2026-04-15", confirmed=False
    )
    assert result["confirmation_required"] is True
    count = conn.execute("SELECT COUNT(*) FROM next_actions").fetchone()[0]
    assert count == 0


def test_create_action_on_unowned_contact_requires_extra_confirmation():
    conn = make_conn()
    seed_contact(conn, owner_id="other_user")  # not owned by CONFIG user
    result = write_helpers.create_next_action(
        CONFIG, conn, "c1", "Send proposal", "2026-04-15", confirmed=False
    )
    assert result["confirmation_required"] is True
    assert result.get("unowned_contact") is True


# ── tag operations ────────────────────────────────────────────────────────────

def test_add_tag_blocked_on_unowned_contact():
    conn = make_conn()
    seed_contact(conn, owner_id="other_user")
    result = write_helpers.add_tag(CONFIG, conn, "c1", "partner", confirmed=True)
    assert result["error"] is not None
    assert "not owned" in result["error"].lower()


def test_remove_tag_blocked_on_unowned_contact():
    conn = make_conn()
    seed_contact(conn, owner_id="other_user")
    result = write_helpers.remove_tag(CONFIG, conn, "c1", "investor", confirmed=True)
    assert result["error"] is not None


def test_add_tag_on_owned_contact_preview():
    conn = make_conn()
    seed_contact(conn, owner_id="uid")
    result = write_helpers.add_tag(CONFIG, conn, "c1", "partner", confirmed=False)
    assert result["confirmation_required"] is True
    assert result.get("error") is None


# ── update_cadence ────────────────────────────────────────────────────────────

def test_update_cadence_always_shows_preview_first():
    conn = make_conn()
    seed_contact(conn)
    result = write_helpers.update_cadence(CONFIG, conn, "c1", 12, confirmed=False)
    assert result["confirmation_required"] is True
    assert "current_cadence_months" in result


def test_update_cadence_blocked_on_unowned_contact():
    conn = make_conn()
    seed_contact(conn, owner_id="other_user")
    result = write_helpers.update_cadence(CONFIG, conn, "c1", 12, confirmed=True)
    assert result["error"] is not None
