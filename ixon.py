"""Ixon Cloud API: authentication and device proxy access."""

import sys
import base64
import os
import requests
from .ui import C, ok, err, warn, heading, pick_from_list, print_numbered_list

IXON_APP_ID   = "9J9IZzeT4xN4"
IXON_API_BASE = "https://api.ayayot.com"


def authenticate() -> tuple[dict, list[dict]]:
    """Login to Ixon, discover devices with InfluxDB. Done once per session.

    Returns (ixon_headers, influx_devices).
    """
    email = os.environ.get("IXON_EMAIL") or input("  Ixon email: ").strip()
    password = os.environ.get("IXON_PASS") or input("  Ixon password: ").strip()

    # Try without 2FA first, then prompt if required
    otp = ""
    while True:
        if otp == "":
            creds = base64.b64encode(f"{email}:{password}".encode()).decode()
        else:
            creds = base64.b64encode(f"{email}:{otp}:{password}".encode()).decode()

        headers = {
            "Api-Version": "2",
            "Api-Application": IXON_APP_ID,
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
        }

        resp = requests.post(
            f"{IXON_API_BASE}/access-tokens",
            headers=headers,
            json={"expiresIn": 3600},
        )

        if resp.status_code == 201:
            break

        # Check if 2FA is required via structured error message
        needs_2fa = False
        try:
            errors = resp.json().get("data", [])
            for e in errors:
                msg = e.get("message", "").lower()
                needs_2fa = "2fa" in msg or "two factor" in msg
                if needs_2fa:
                    break
        except (ValueError, AttributeError):
            pass

        if needs_2fa:
            if otp == "":
                otp = input(f"  Ixon 2FA code {C.DIM}(required for this account){C.RESET}: ").strip()
                if otp:
                    continue
            else:
                err("Invalid 2FA code.")
                otp = input(f"  Ixon 2FA code: ").strip()
                if otp:
                    continue

        err(f"Ixon login failed — HTTP {resp.status_code}: {resp.text[:200]}")
        sys.exit(1)

    token = resp.json()["data"]["secretId"]
    headers["Authorization"] = f"Bearer {token}"
    ok("Ixon authenticated. Token valid for 1 hour.")

    # Company
    resp2 = requests.get(
        f"{IXON_API_BASE}/companies",
        headers=headers,
        params={"fields": "publicId,name"},
    )
    headers["Api-Company"] = resp2.json()["data"][0]["publicId"]

    # Discover devices with InfluxDB servers
    print(f"  {C.DIM}Discovering devices...{C.RESET}")
    resp3 = requests.get(
        f"{IXON_API_BASE}/agents",
        headers=headers,
        params={"fields": "publicId,name"},
    )

    devices = []
    for agent in resp3.json()["data"]:
        resp4 = requests.get(
            f"{IXON_API_BASE}/agents/{agent['publicId']}/servers",
            headers=headers,
        )
        if resp4.status_code == 200:
            for server in resp4.json()["data"]:
                if "influx" in server.get("name", "").lower():
                    devices.append({
                        "agent_name": agent["name"],
                        "agent_id": agent["publicId"],
                        "server_id": server["publicId"],
                        "server_name": server["name"],
                    })

    if not devices:
        err("No InfluxDB servers found on any device.")
        sys.exit(1)

    ok(f"Found {len(devices)} device(s) with InfluxDB.")
    return headers, devices


def pick_device(ixon_headers: dict, devices: list[dict],
                cfg: dict) -> tuple[requests.Session, str, str]:
    """Let user pick a device, create WebAccess proxy session.

    Auto re-authenticates if the Ixon token has expired.
    Returns (session, proxy_base_url, device_display_name).
    """
    names = [f"{d['agent_name']} ({d['server_name']})" for d in devices]
    last = cfg.get("device", "")

    heading("Select device")
    print(f"\n  Available devices ({len(devices)}):")
    print_numbered_list(names, last)

    chosen = pick_from_list(names, "Select device", last)
    device = devices[names.index(chosen)]

    # WebAccess session
    print(f"  {C.DIM}Connecting to {device['agent_name']}...{C.RESET}")
    resp = requests.post(
        f"{IXON_API_BASE}/web-access",
        headers=ixon_headers,
        json={"server": {"publicId": device["server_id"]}},
    )

    if resp.status_code in (401, 403):
        warn("Ixon session expired. Re-authenticating...")
        new_headers, new_devices = authenticate()
        ixon_headers.clear()
        ixon_headers.update(new_headers)
        devices.clear()
        devices.extend(new_devices)
        return pick_device(ixon_headers, devices, cfg)

    if resp.status_code != 201:
        msg = resp.json().get("data", [{}])[0].get("message", resp.text[:200])
        err(f"WebAccess failed: {msg}")
        if "not online" in msg.lower():
            print(f"    {C.DIM}The device is offline. Try a different one.{C.RESET}")
        return pick_device(ixon_headers, devices, cfg)

    proxy_url = resp.json()["data"]["url"]
    base_url = proxy_url.split("?")[0].rstrip("/")

    # InfluxDB API token — check saved config, then .env, then prompt
    device_key = device["agent_name"]
    api_token = (cfg.get("device_tokens", {}).get(device_key, "")
                 or os.environ.get("API_TOKEN", ""))
    if not api_token:
        warn(f"No API token saved for {device_key}.")
        print(f"    {C.DIM}Find it in InfluxDB -> Load Data -> API Tokens{C.RESET}")
        api_token = input("  InfluxDB API token: ").strip()
        if not api_token:
            err("Cannot connect without an API token.")
            return pick_device(ixon_headers, devices, cfg)
        # Save for next time
        device_tokens = cfg.get("device_tokens", {})
        device_tokens[device_key] = api_token
        cfg["device_tokens"] = device_tokens

    session = requests.Session()
    session.headers["Authorization"] = f"Token {api_token}"
    session.get(proxy_url, timeout=15)

    # Verify connection — prompt for new token on 401
    test = session.get(f"{base_url}/api/v2/buckets", timeout=15)
    if test.status_code == 200:
        ok(f"Connected to {device['agent_name']}.")
    elif test.status_code == 401:
        warn(f"API token rejected (401) for {device_key}.")
        print(f"    {C.DIM}Generate a new token in InfluxDB -> Load Data -> API Tokens{C.RESET}")
        api_token = input("  Enter a new InfluxDB API token: ").strip()
        if not api_token:
            err("Cannot connect without a valid API token.")
            return pick_device(ixon_headers, devices, cfg)
        # Update saved token and retry
        device_tokens = cfg.get("device_tokens", {})
        device_tokens[device_key] = api_token
        cfg["device_tokens"] = device_tokens
        session.headers["Authorization"] = f"Token {api_token}"
        test2 = session.get(f"{base_url}/api/v2/buckets", timeout=15)
        if test2.status_code == 200:
            ok(f"Connected to {device['agent_name']}.")
        else:
            err(f"Still failing (HTTP {test2.status_code}). Check the token and try again.")
            return pick_device(ixon_headers, devices, cfg)
    else:
        warn(f"Proxy returned HTTP {test.status_code} — downloads may fail.")

    return session, base_url, chosen
