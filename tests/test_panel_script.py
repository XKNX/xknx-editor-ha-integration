"""The panel script loaded a second time (its URL changes with a new version, and Home Assistant
loads it again after a restart) leaves the element defined first in place instead of failing."""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "custom_components/xknx_editor/frontend/panel.js"

# A browser's custom element registry, as far as the script uses it: a name can be defined once.
HARNESS = """
globalThis.HTMLElement = class {};
const defined = new Map();
globalThis.customElements = {
  get: (name) => defined.get(name),
  define: (name, cls) => {
    if (defined.has(name)) throw new Error(`the name "${name}" has already been used`);
    defined.set(name, cls);
  },
};
const url = process.argv[1];
await import(url + "?v=1");
const first = defined.get("xknx-editor-panel");
await import(url + "?v=2");
if (defined.get("xknx-editor-panel") !== first) throw new Error("replaced");
console.log("ok");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_loading_the_script_again_keeps_the_element(tmp_path: Path) -> None:
    script = tmp_path / "panel.mjs"
    script.write_bytes(SCRIPT.read_bytes())
    result = subprocess.run(
        ["node", "--input-type=module", "-e", HARNESS, script.as_uri()],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
