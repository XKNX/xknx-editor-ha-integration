"""Starting and stopping the editor process, with a stand-in for the editor."""

import json
import sys
from pathlib import Path

import aiohttp
import pytest

from custom_components.xknx_editor.process import EditorProcess, StartError

# A stand-in for `python -m editor_gui.main`: answers on its --web port, reports its arguments and
# environment, and ends on SIGTERM. The process runs in the data folder, so `-m` finds it there.
FAKE_EDITOR = """
import http.server, json, os, signal, sys
web = next(a for a in sys.argv if a.startswith("--web="))[6:]
host, port = web.rsplit(":", 1)
with open("seen.json", "w") as f:
    json.dump({"argv": sys.argv[1:], "password": os.environ.get("XKNX_WEB_PASSWORD"),
               "data": os.environ.get("XDG_DATA_HOME"), "cache": os.environ.get("XDG_CACHE_HOME"),
               "pythonpath": os.environ.get("PYTHONPATH")}, f)
if os.environ.get("FAKE_FAIL"):
    print("[error    ] cannot start", flush=True)
    sys.exit(3)
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *args): pass
server = http.server.HTTPServer((host, int(port)), Handler)
signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
print("\\x1b[32mlistening\\x1b[0m", flush=True)
server.serve_forever()
"""


# Real sockets on 127.0.0.1: the editor's stand-in listens on one.
pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.fixture
def editor(tmp_path: Path) -> EditorProcess:
    package = tmp_path / "data" / "editor_gui"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "main.py").write_text(FAKE_EDITOR)
    return EditorProcess(
        Path(sys.executable),
        data=tmp_path / "data",
        files=tmp_path / "data" / "files",
        cache=tmp_path / "cache",
        password="secret",
    )


async def test_start_and_stop(editor: EditorProcess, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
    async with aiohttp.ClientSession() as session:
        await editor.async_start(session)
        assert editor.running
        async with session.get(editor.url) as response:
            assert await response.text() == "ok"
    seen = json.loads((tmp_path / "data" / "seen.json").read_text())
    assert seen["argv"][0] == f"--web=127.0.0.1:{editor.port}"
    assert "--web-proxy-ip=127.0.0.1" in seen["argv"]
    assert f"--web-files={tmp_path / 'data' / 'files'}" in seen["argv"]
    assert seen["password"] == "secret"
    assert seen["data"] == str(tmp_path / "data" / "data")
    assert seen["cache"] == str(tmp_path / "cache")
    # Home Assistant's module path does not reach the editor.
    assert seen["pythonpath"] is None
    assert (tmp_path / "data" / "files").is_dir()
    await editor.async_stop()
    assert not editor.running
    assert "listening" in editor.output


async def test_start_failure_reports_the_output(editor: EditorProcess, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_FAIL", "1")
    async with aiohttp.ClientSession() as session:
        with pytest.raises(StartError, match="cannot start"):
            await editor.async_start(session)
    assert not editor.running
