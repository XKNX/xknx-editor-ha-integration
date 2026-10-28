"""Onboard the test Home Assistant on localhost:8123 (user dev, password dev-password) and add XKNX
Editor; with an argument, set its requirements option to it (a URL, or a path on the Home
Assistant host, e.g. from editor_requirements.py). Tokens go to .dev/ of the repository."""

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://localhost:8123"
CLIENT = f"{BASE}/"
# Tokens and the like, next to the repository's other local files.
HERE = Path(__file__).resolve().parents[2] / ".dev"
HERE.mkdir(exist_ok=True)


def call(
    method: str, path: str, body: object = None, token: str = "", form: bool = False
) -> object:
    data = None
    headers = {}
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=60) as response:
        text = response.read().decode()
        return json.loads(text) if text else None


steps = {s["step"]: s["done"] for s in call("GET", "/api/onboarding")}  # type: ignore[union-attr]
if not steps.get("user"):
    code = call(
        "POST",
        "/api/onboarding/users",
        {
            "client_id": CLIENT,
            "name": "Dev",
            "username": "dev",
            "password": "dev-password",
            "language": "en",
        },
    )["auth_code"]  # type: ignore[index]
    tokens = call(
        "POST",
        "/auth/token",
        {"grant_type": "authorization_code", "code": code, "client_id": CLIENT},
        form=True,
    )
    token = tokens["access_token"]  # type: ignore[index]
    call("POST", "/api/onboarding/core_config", {}, token)
    call("POST", "/api/onboarding/analytics", {}, token)
    call(
        "POST", "/api/onboarding/integration", {"client_id": CLIENT, "redirect_uri": CLIENT}, token
    )
    # A long-lived token for the next runs.
    (HERE / "refresh.txt").write_text(tokens["refresh_token"])  # type: ignore[index]
refresh = (HERE / "refresh.txt").read_text().strip()
token = call(
    "POST",
    "/auth/token",
    {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": CLIENT},
    form=True,
)["access_token"]  # type: ignore[index]
(HERE / "token.txt").write_text(token)

entries = [
    e
    for e in call("GET", "/api/config/config_entries/entry", token=token)
    if e["domain"] == "xknx_editor"
]  # type: ignore[union-attr]
if not entries:
    flow = call("POST", "/api/config/config_entries/flow", {"handler": "xknx_editor"}, token)
    print("flow:", flow["type"], flow.get("step_id"))  # type: ignore[index]
    result = call("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", {}, token)
    print("entry:", result["type"], result.get("title"))  # type: ignore[index]
    entries = [
        e
        for e in call("GET", "/api/config/config_entries/entry", token=token)
        if e["domain"] == "xknx_editor"
    ]  # type: ignore[union-attr]
entry_id = entries[0]["entry_id"]
if len(sys.argv) > 1:
    flow = call("POST", "/api/config/config_entries/options/flow", {"handler": entry_id}, token)
    result = call(
        "POST",
        f"/api/config/config_entries/options/flow/{flow['flow_id']}",  # type: ignore[index]
        {"requirements": sys.argv[1]},
        token,
    )
    print("options:", result["type"])  # type: ignore[index]
print("entry state:", entries[0]["state"])
