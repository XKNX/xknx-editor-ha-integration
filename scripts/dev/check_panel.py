"""Open the XKNX Editor panel of the test Home Assistant (signed in with the token of setup_ha.py)
in a browser and take screenshots:

    uv run --with playwright scripts/dev/check_panel.py .dev/shots [width height scheme ...]

argv: output folder, then width, height and color scheme triples (default desktop and phone).
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8123"
# Tokens and the like, next to the repository's other local files.
HERE = Path(__file__).resolve().parents[2] / ".dev"
HERE.mkdir(exist_ok=True)
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
sizes = sys.argv[2:] or ["1400", "900", "light", "390", "844", "dark"]

refresh = (HERE / "refresh.txt").read_text().strip()
body = urllib.parse.urlencode(
    {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": f"{BASE}/"}
).encode()
with urllib.request.urlopen(f"{BASE}/auth/token", body, timeout=30) as response:
    token = json.load(response)
tokens = {
    "access_token": token["access_token"],
    "token_type": "Bearer",
    "expires_in": token["expires_in"],
    "refresh_token": refresh,
    "expires": int(time.time() * 1000) + token["expires_in"] * 1000,
    "hassUrl": BASE,
    "clientId": f"{BASE}/",
}

with sync_playwright() as p:
    browser = p.chromium.launch()
    for i in range(0, len(sizes), 3):
        width, height, scheme = int(sizes[i]), int(sizes[i + 1]), sizes[i + 2]
        context = browser.new_context(
            viewport={"width": width, "height": height}, color_scheme=scheme
        )
        context.add_init_script(
            f"localStorage.setItem('hassTokens', {json.dumps(json.dumps(tokens))});"
        )
        page = context.new_page()
        messages: list[str] = []
        page.on("console", lambda m, messages=messages: messages.append(f"{m.type}: {m.text}"))
        page.goto(f"{BASE}/xknx-editor")
        page.wait_for_timeout(8000)
        name = f"panel-{width}x{height}-{scheme}"
        page.screenshot(path=str(out / f"{name}.png"))
        frame = page.frame_locator("xknx-editor-panel >> iframe")
        try:
            canvas = frame.locator("canvas").count()
        except Exception as error:
            canvas = f"error {error}"
        cookies = [c["name"] + " " + c["path"] for c in context.cookies()]
        print(name, "canvas:", canvas, "cookies:", cookies)
        for message in messages[-8:]:
            print("  ", message[:200])
        context.close()
    browser.close()
