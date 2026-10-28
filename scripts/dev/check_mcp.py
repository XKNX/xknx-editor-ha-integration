"""Talk MCP to the editor through the test Home Assistant (signed in with the token of
setup_ha.py): initialize, list the tools, call one.

    python scripts/dev/check_mcp.py [tool name]

argv: optionally the tool to call without arguments (default: project_info).
"""

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://localhost:8123"
MCP = f"{BASE}/api/xknx_editor/mcp"
DEV = Path(__file__).resolve().parents[2] / ".dev"

refresh = (DEV / "refresh.txt").read_text().strip()
body = urllib.parse.urlencode(
    {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": f"{BASE}/"}
).encode()
with urllib.request.urlopen(f"{BASE}/auth/token", body, timeout=30) as response:
    token = json.load(response)["access_token"]
session: str | None = None


def rpc(method: str, params: dict[str, object], number: int | None) -> object:
    """One JSON-RPC message; the result of a request (event stream or JSON), None otherwise."""
    global session
    message: dict[str, object] = {"jsonrpc": "2.0", "method": method, "params": params}
    if number is not None:
        message["id"] = number
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session:
        headers["Mcp-Session-Id"] = session
    request = urllib.request.Request(MCP, json.dumps(message).encode(), headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        session = response.headers.get("Mcp-Session-Id") or session
        text = response.read().decode()
    if number is None:
        return None
    if text.lstrip().startswith("{"):
        return json.loads(text)
    data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
    return json.loads(data[-1])


info = rpc(
    "initialize",
    {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "check_mcp", "version": "1"},
    },
    1,
)
print("server:", info["result"]["serverInfo"])  # type: ignore[index]
rpc("notifications/initialized", {}, None)
tools = rpc("tools/list", {}, 2)["result"]["tools"]  # type: ignore[index]
print(len(tools), "tools, e.g.", ", ".join(t["name"] for t in tools[:6]))
name = sys.argv[1] if len(sys.argv) > 1 else "project_info"
result = rpc("tools/call", {"name": name, "arguments": {}}, 3)
print(name, "->", json.dumps(result)[:400])
