import json
import time
import requests
from pathlib import Path

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.json"


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


def get_graph_token(config, config_path=None):
    """Return a valid Graph access token. Refreshes or runs device flow as needed."""
    if (config.get("graph_access_token")
            and config.get("graph_token_expiry", 0) > time.time() + 60):
        return config["graph_access_token"]

    if config.get("graph_refresh_token"):
        return _refresh_token(config, config_path)

    return _run_device_flow(config, config_path)


def _client_params(config):
    """Base params included in all token requests — adds secret if configured."""
    params = {"client_id": config["graph_client_id"]}
    secret = config.get("graph_client_secret", "")
    if secret:
        params["client_secret"] = secret
    return params


def _refresh_token(config, config_path=None):
    r = requests.post(
        f"https://login.microsoftonline.com/{config['graph_tenant_id']}/oauth2/v2.0/token",
        data={
            **_client_params(config),
            "grant_type": "refresh_token",
            "refresh_token": config["graph_refresh_token"],
            "scope": "Mail.Read User.Read offline_access",
        },
    )
    r.raise_for_status()
    data = r.json()
    config["graph_access_token"] = data["access_token"]
    config["graph_refresh_token"] = data.get("refresh_token", config["graph_refresh_token"])
    config["graph_token_expiry"] = time.time() + data.get("expires_in", 3600)
    save_config(config, config_path)
    return config["graph_access_token"]


def _run_device_flow(config, config_path=None):
    r = requests.post(
        f"https://login.microsoftonline.com/{config['graph_tenant_id']}/oauth2/v2.0/devicecode",
        data={
            **_client_params(config),
            "scope": "Mail.Read User.Read offline_access",
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
            f"https://login.microsoftonline.com/{config['graph_tenant_id']}/oauth2/v2.0/token",
            data={
                **_client_params(config),
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": flow["device_code"],
            },
        )
        data = poll.json()
        if "access_token" in data:
            config["graph_access_token"] = data["access_token"]
            config["graph_refresh_token"] = data.get("refresh_token", "")
            config["graph_token_expiry"] = time.time() + data.get("expires_in", 3600)
            save_config(config, config_path)
            print("Outlook authentication successful.")
            return config["graph_access_token"]
        if data.get("error") == "slow_down":
            interval += 5
        elif data.get("error") != "authorization_pending":
            raise RuntimeError(
                f"Device flow failed: {data.get('error_description', data.get('error'))}"
            )

    raise RuntimeError("Device flow timed out. Re-run sync to try again.")
