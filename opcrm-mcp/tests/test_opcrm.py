import sys
import os
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import opcrm

CONFIG = {"opcrm_user_id": "uid", "opcrm_api_key": "key"}


def mock_response(data):
    m = MagicMock()
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


def test_fetch_all_contacts_single_page():
    contacts_payload = [
        {"contact": {"id": "c1", "first_name": "Alice", "last_name": "Smith",
                     "emails": [{"value": "alice@acme.com"}], "company_name": "Acme",
                     "tags": ["investor"], "custom_fields": [], "owner_id": "u1",
                     "status": "active"}},
    ]
    with patch("requests.get", return_value=mock_response(
        {"data": {"contacts": contacts_payload}}
    )):
        result = opcrm.fetch_all_contacts(CONFIG)
    assert len(result) == 1
    assert result[0]["contact"]["id"] == "c1"


def test_fetch_notes_for_contact():
    notes_payload = [{"id": "n1", "contact_id": "c1", "text": "Called", "date": "2026-01-10", "author_id": "u1"}]
    with patch("requests.get", return_value=mock_response(
        {"data": {"notes": notes_payload}}
    )):
        result = opcrm.fetch_notes(CONFIG, "c1")
    assert len(result) == 1
    assert result[0]["id"] == "n1"


def test_create_note_posts_correct_payload():
    with patch("requests.post", return_value=mock_response({"data": {"note": {"id": "n2"}}})) as mock_post:
        opcrm.create_note(CONFIG, "c1", "Follow up on proposal")
    call_json = mock_post.call_args[1]["json"]
    assert call_json["note"]["contact_id"] == "c1"
    assert call_json["note"]["text"] == "Follow up on proposal"


def test_add_tag_appends_to_existing():
    existing = {"contact": {"id": "c1", "tags": ["investor"]}}
    with patch("requests.get", return_value=mock_response({"data": existing})):
        with patch("requests.put", return_value=mock_response({"data": {}})) as mock_put:
            opcrm.add_tag(CONFIG, "c1", "board")
    tags_sent = mock_put.call_args[1]["json"]["contact"]["tags"]
    assert set(tags_sent) == {"investor", "board"}


def test_remove_tag_strips_tag():
    existing = {"contact": {"id": "c1", "tags": ["investor", "board"]}}
    with patch("requests.get", return_value=mock_response({"data": existing})):
        with patch("requests.put", return_value=mock_response({"data": {}})) as mock_put:
            opcrm.remove_tag(CONFIG, "c1", "investor")
    tags_sent = mock_put.call_args[1]["json"]["contact"]["tags"]
    assert tags_sent == ["board"]
