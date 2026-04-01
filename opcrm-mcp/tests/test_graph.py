import sys
import os
from unittest.mock import patch, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import graph


def mock_response(data, status=200):
    m = MagicMock()
    m.json.return_value = data
    m.status_code = status
    m.raise_for_status = MagicMock()
    if status == 401:
        m.raise_for_status.side_effect = Exception("401")
    return m


def test_fetch_emails_single_page():
    payload = {
        "value": [
            {
                "id": "msg1",
                "subject": "Proposal follow-up",
                "bodyPreview": "Hi Peter, following up...",
                "receivedDateTime": "2026-03-01T10:00:00Z",
                "from": {"emailAddress": {"address": "alice@acme.com"}},
                "toRecipients": [{"emailAddress": {"address": "pmoon@navicet.com"}}],
                "isDraft": False,
                "threadId": "thread1",
            }
        ]
    }
    with patch("requests.get", return_value=mock_response(payload)):
        emails = graph.fetch_emails("tok", folder="me/messages")
    assert len(emails) == 1
    assert emails[0]["id"] == "msg1"


def test_fetch_emails_skips_drafts():
    payload = {
        "value": [
            {"id": "d1", "isDraft": True, "subject": "Draft", "bodyPreview": "",
             "receivedDateTime": "2026-03-01T10:00:00Z",
             "from": {"emailAddress": {"address": "x@y.com"}},
             "toRecipients": [], "threadId": "t1"},
        ]
    }
    with patch("requests.get", return_value=mock_response(payload)):
        emails = graph.fetch_emails("tok")
    assert len(emails) == 0


def test_fetch_emails_raises_on_401():
    with patch("requests.get", return_value=mock_response({}, status=401)):
        try:
            graph.fetch_emails("expired_token")
            assert False, "Should have raised"
        except RuntimeError as e:
            assert "expired" in str(e).lower()


def test_fetch_emails_follows_next_link():
    page1 = {
        "value": [{"id": "m1", "isDraft": False, "subject": "S1", "bodyPreview": "",
                   "receivedDateTime": "2026-03-01T10:00:00Z",
                   "from": {"emailAddress": {"address": "a@b.com"}},
                   "toRecipients": [], "threadId": "t1"}],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skip=100"
    }
    page2 = {
        "value": [{"id": "m2", "isDraft": False, "subject": "S2", "bodyPreview": "",
                   "receivedDateTime": "2026-03-01T09:00:00Z",
                   "from": {"emailAddress": {"address": "a@b.com"}},
                   "toRecipients": [], "threadId": "t2"}]
    }
    responses = [mock_response(page1), mock_response(page2)]
    with patch("requests.get", side_effect=responses):
        emails = graph.fetch_emails("tok")
    assert len(emails) == 2
