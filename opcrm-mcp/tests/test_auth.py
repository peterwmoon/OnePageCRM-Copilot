import json
import time
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import auth


def make_config(tmp_path):
    config = {
        "opcrm_user_id": "uid",
        "opcrm_api_key": "key",
        "opcrm_my_user_id": "uid",
        "graph_client_id": "client123",
        "graph_tenant_id": "tenant456",
        "graph_access_token": "",
        "graph_refresh_token": "",
        "graph_token_expiry": 0
    }
    p = Path(tmp_path) / "config.json"
    p.write_text(json.dumps(config))
    return config, str(p)


def test_load_config(tmp_path):
    config, path = make_config(tmp_path)
    loaded = auth.load_config(path)
    assert loaded["opcrm_user_id"] == "uid"
    assert loaded["graph_client_id"] == "client123"


def test_save_config(tmp_path):
    config, path = make_config(tmp_path)
    config["graph_access_token"] = "tok"
    auth.save_config(config, path)
    reloaded = auth.load_config(path)
    assert reloaded["graph_access_token"] == "tok"


def test_get_graph_token_returns_valid_cached_token(tmp_path):
    config, path = make_config(tmp_path)
    config["graph_access_token"] = "cached_token"
    config["graph_token_expiry"] = time.time() + 3600
    token = auth.get_graph_token(config, path)
    assert token == "cached_token"


def test_get_graph_token_refreshes_expired_token(tmp_path):
    config, path = make_config(tmp_path)
    config["graph_access_token"] = "old_token"
    config["graph_refresh_token"] = "refresh_tok"
    config["graph_token_expiry"] = time.time() - 1  # expired

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "access_token": "new_token",
        "refresh_token": "new_refresh",
        "expires_in": 3600
    }
    mock_response.raise_for_status = MagicMock()

    with patch("requests.post", return_value=mock_response) as mock_post:
        token = auth.get_graph_token(config, path)

    assert token == "new_token"
    mock_post.assert_called_once()
    call_data = mock_post.call_args[1]["data"]
    assert call_data["grant_type"] == "refresh_token"
    assert call_data["refresh_token"] == "refresh_tok"

    saved = auth.load_config(path)
    assert saved["graph_access_token"] == "new_token"
