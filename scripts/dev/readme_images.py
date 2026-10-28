"""The README images: the editor's main screenshots (from its repository's README, 1680x1000 CSS
pixels at scale 2) with Home Assistant's sidebar next to them, as the panel shows the editor at
that size. The sidebar comes from the test Home Assistant (setup_ha.py), in the same theme:

    uv run --with playwright --with pillow scripts/dev/readme_images.py \
        ../xknx-editor/docs/images images

argv: the editor's README images folder, output folder.
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8123"
DEV = Path(__file__).resolve().parents[2] / ".dev"
SCALE = 2
SIDEBAR = 256  # CSS pixels: Home Assistant's expanded sidebar
EDITOR = (1680, 1000)  # CSS pixels of the editor's main screenshots
SHOTS = {"dark": ("editor.png", "editor.png"), "light": ("editor-light.png", "editor-light.png")}

source, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
refresh = (DEV / "refresh.txt").read_text().strip()
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
    for scheme, (editor_name, out_name) in SHOTS.items():
        editor = Image.open(source / editor_name).convert("RGB")
        assert editor.size == (EDITOR[0] * SCALE, EDITOR[1] * SCALE), editor.size
        context = browser.new_context(
            viewport={"width": SIDEBAR + EDITOR[0], "height": EDITOR[1]},
            device_scale_factor=SCALE,
            color_scheme=scheme,
            locale="en-US",
        )
        context.add_init_script(
            f"localStorage.setItem('hassTokens', {json.dumps(json.dumps(tokens))});"
        )
        page = context.new_page()
        page.goto(f"{BASE}/xknx-editor")
        page.wait_for_timeout(6000)
        shot = Image.open(BytesIO(page.screenshot())).convert("RGB")
        context.close()
        image = Image.new("RGB", ((SIDEBAR + EDITOR[0]) * SCALE, EDITOR[1] * SCALE))
        image.paste(shot.crop((0, 0, SIDEBAR * SCALE, EDITOR[1] * SCALE)), (0, 0))
        image.paste(editor, (SIDEBAR * SCALE, 0))
        image.save(out / out_name, optimize=True)
        print(out / out_name, image.size)
    browser.close()
