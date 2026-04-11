import json
import time
import requests
from pathlib import Path

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.json"

_WORK_SCOPE = "Mail.Read Calendars.Read User.Read offline_access"
_PERSONAL_SCOPE = "Mail.Read User.Read offline_access"
_CONSUMERS_ENDPOINT = "https://login.microsoftonline.com/consumers/oauth2/v2.0"


def load_config(config_path=None):
    path = Path(config_path or _DEFAULT_CONFIG_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"config.json not found at {path}. "
            "Copy config.json.template, rename it config.json, and fill in your credentials."
        )
    with open(path) as f:
        return json.load(f)


def save_config(config, config_path=None):
    path = Path(config_path or _DEFAULT_CONFIG_PATH)
    with open(path, "w") as f:
        json.dump(config, f, indent=2)


def _client_params(config):
    """Base params included in all token requests — adds secret if configured."""
    params = {"client_id": config["graph_client_id"]}
    secret = config.get("graph_client_secret", "")
    if secret:
        params["client_secret"] = secret
    return params


def _work_flow_params(config):
    """OAuth params for the work account (tenant-specific endpoint)."""
    endpoint = f"https://login.microsoftonline.com/{config['graph_tenant_id']}/oauth2/v2.0"
    return {
        "endpoint": endpoint,
        "token_key": "graph_access_token",
        "refresh_key": "graph_refresh_token",
        "expiry_key": "graph_token_expiry",
        "scope": _WORK_SCOPE,
    }


def _personal_flow_params():
    """OAuth params for the personal account (consumers endpoint)."""
    return {
        "endpoint": _CONSUMERS_ENDPOINT,
        "token_key": "graph_access_token_personal",
        "refresh_key": "graph_refresh_token_personal",
        "expiry_key": "graph_token_expiry_personal",
        "scope": _PERSONAL_SCOPE,
    }


def _refresh_token(config, config_path, *, endpoint, token_key, refresh_key, expiry_key, scope):
    r = requests.post(
        f"{endpoint}/token",
        data={
            **_client_params(config),
            "grant_type": "refresh_token",
            "refresh_token": config[refresh_key],
            "scope": scope,
        },
    )
    r.raise_for_status()
    data = r.json()
    config[token_key] = data["access_token"]
    config[refresh_key] = data.get("refresh_token", config[refresh_key])
    config[expiry_key] = time.time() + data.get("expires_in", 3600)
    save_config(config, config_path)
    return config[token_key]


def _run_device_flow(config, config_path, *, endpoint, token_key, refresh_key, expiry_key, scope):
    r = requests.post(
        f"{endpoint}/devicecode",
        data={
            "client_id": config["graph_client_id"],
            "scope": scope,
        },
    )
    r.raise_for_status()
    flow = r.json()

    print(f"\nTo authorize Outlook access, visit: {flow['verification_uri']}")
    print(f"Enter this code: {flow['user_code']}\n")

    interval = flow.get("interval", 5)
    deadline = time.time() + flow.get("expires_in", 900)

    while time.time() < deadline:
        time.sleep(interval)
        poll = requests.post(
            f"{endpoint}/token",
            data={
                "client_id": config["graph_client_id"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": flow["device_code"],
            },
        )
        data = poll.json()
        if "access_token" in data:
            config[token_key] = data["access_token"]
            config[refresh_key] = data.get("refresh_token", "")
            config[expiry_key] = time.time() + data.get("expires_in", 3600)
            save_config(config, config_path)
            print("Outlook authentication successful.")
            return config[token_key]
        if data.get("error") == "slow_down":
            interval += 5
        elif data.get("error") != "authorization_pending":
            raise RuntimeError(
                f"Device flow failed: {data.get('error_description', data.get('error'))}"
            )

    raise RuntimeError("Device flow timed out. Re-run sync to try again.")


def _get_token(config, config_path, params):
    """Core token retrieval logic — shared by work and personal flows."""
    token_key = params["token_key"]
    expiry_key = params["expiry_key"]
    refresh_key = params["refresh_key"]

    if config.get(token_key) and config.get(expiry_key, 0) > time.time() + 60:
        return config[token_key]

    # Re-read disk in case an external auth script updated tokens without restarting the server
    path = Path(config_path or _DEFAULT_CONFIG_PATH)
    if path.exists():
        fresh = json.load(open(path))
        if fresh.get(token_key) and fresh.get(expiry_key, 0) > time.time() + 60:
            config.update(fresh)
            return config[token_key]
        if fresh.get(refresh_key) and not config.get(refresh_key):
            config.update(fresh)

    if config.get(refresh_key):
        return _refresh_token(config, config_path, **params)
    return _run_device_flow(config, config_path, **params)


def get_graph_token(config, config_path=None):
    """Return a valid Graph access token for the work account. Refreshes or runs device flow as needed."""
    return _get_token(config, config_path, _work_flow_params(config))


def get_graph_token_personal(config, config_path=None):
    """Return a valid Graph access token for the personal account (consumers endpoint)."""
    return _get_token(config, config_path, _personal_flow_params())
