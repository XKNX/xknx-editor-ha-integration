"""Starting and stopping the editor process, with a stand-in for the editor."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import aiohttp
import pytest

from custom_components.xknx_editor import process
from custom_components.xknx_editor.process import EditorProcess, StartError

# A stand-in for `python -m editor_gui.main`: answers on its --web port, reports its arguments and
# environment, and ends on SIGTERM. The process runs in the data folder, so `-m` finds it there.
FAKE_EDITOR = """
import http.server, json, os, signal, sys
web = next(a for a in sys.argv if a.startswith("--web="))[6:]
host, port = web.rsplit(":", 1)
with open("seen.json", "w") as f:
    json.dump({"argv": sys.argv[1:], "password": os.environ.get("XKNX_WEB_PASSWORD"),
               "mcp_token": os.environ.get("XKNX_MCP_AUTH_TOKEN"),
               "path": os.environ.get("PATH"), "helpers": os.environ.get("XKNX_KEYEXTRACT_DIR"),
               "data": os.environ.get("XDG_DATA_HOME"), "cache": os.environ.get("XDG_CACHE_HOME"),
               "pythonpath": os.environ.get("PYTHONPATH")}, f)
if os.environ.get("FAKE_FAIL"):
    print("[error    ] cannot start", flush=True)
    sys.exit(3)
if os.environ.get("FAKE_SILENT"):
    import time
    time.sleep(60)
if os.environ.get("FAKE_STUBBORN"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
else:
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *args): pass
server = http.server.HTTPServer((host, int(port)), Handler)
print("", flush=True)
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
        mcp_token="mcp-secret",
        mcp_url="https://ha.example/api/xknx_editor/mcp",
        environment={"DOTNET_ROOT": "/opt/dotnet", "XKNX_KEYEXTRACT_DIR": "/opt/keyextract"},
        language="pt-BR",
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
    # Inside Home Assistant, with the MCP server on a port of its own and its token.
    assert "--home-assistant" in seen["argv"]
    # The only editor here: started without offering others first.
    assert "--new-instance" in seen["argv"]
    assert f"--mcp=127.0.0.1:{editor.mcp_port}" in seen["argv"]
    assert editor.mcp_port not in (0, editor.port)
    assert editor.mcp_endpoint == f"http://127.0.0.1:{editor.mcp_port}/mcp"
    assert "--mcp-url=https://ha.example/api/xknx_editor/mcp" in seen["argv"]
    assert seen["mcp_token"] == "mcp-secret"
    # Home Assistant's language, for the editor's while none is chosen in it.
    assert "--language=pt-BR" in seen["argv"]
    # The .NET runtime first on the path, the helpers named.
    assert seen["path"].split(os.pathsep)[0] == "/opt/dotnet"
    assert seen["helpers"] == "/opt/keyextract"
    assert seen["data"] == str(tmp_path / "data" / "data")
    assert seen["cache"] == str(tmp_path / "cache")
    # Home Assistant's module path does not reach the editor.
    assert seen["pythonpath"] is None
    assert (tmp_path / "data" / "files").is_dir()
    assert editor._process is not None
    assert (tmp_path / "data" / "editor.pid").read_text() == str(editor._process.pid)
    await editor.async_stop()
    assert not editor.running
    # Without its colors, and without empty lines.
    assert list(editor.output) == ["listening"]
    # Stopping an editor that ended does nothing.
    await editor.async_stop()


async def test_start_failure_reports_the_output(editor: EditorProcess, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_FAIL", "1")
    async with aiohttp.ClientSession() as session:
        with pytest.raises(StartError, match="cannot start"):
            await editor.async_start(session)
    assert not editor.running


async def test_an_editor_that_does_not_answer_is_stopped(
    editor: EditorProcess, monkeypatch
) -> None:
    monkeypatch.setenv("FAKE_SILENT", "1")
    monkeypatch.setattr(process, "_START_TIMEOUT", 1.0)
    async with aiohttp.ClientSession() as session:
        with pytest.raises(StartError, match="The editor did not answer"):
            await editor.async_start(session)
    assert not editor.running


async def test_wait_gives_the_exit_code(editor: EditorProcess) -> None:
    async with aiohttp.ClientSession() as session:
        await editor.async_start(session)
    assert editor._process is not None
    editor._process.terminate()
    assert await editor.async_wait() == 0
    assert not editor.running
    # The output was read to its end.
    assert "listening" in editor.output


async def test_an_editor_that_does_not_end_is_killed(
    editor: EditorProcess, monkeypatch, caplog
) -> None:
    monkeypatch.setenv("FAKE_STUBBORN", "1")
    monkeypatch.setattr(process, "_STOP_TIMEOUT", 0.5)
    async with aiohttp.ClientSession() as session:
        await editor.async_start(session)
    await editor.async_stop()
    assert not editor.running
    assert "did not end in time, killing it" in caplog.text


async def _leftover(tmp_path: Path, **environment: str) -> asyncio.subprocess.Process:
    """An editor an earlier run left behind, answering on its port, with its pid file."""
    leftover = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "editor_gui.main",
        f"--web=127.0.0.1:{process.free_port()}",
        cwd=tmp_path / "data",
        env={**os.environ, **environment},
        stdout=asyncio.subprocess.PIPE,
    )
    assert leftover.stdout is not None
    # Its signal handler is set once it writes.
    await leftover.stdout.readline()
    (tmp_path / "data" / "editor.pid").write_text(str(leftover.pid))
    return leftover


async def test_a_leftover_editor_is_ended(editor: EditorProcess, tmp_path: Path, caplog) -> None:
    leftover = await _leftover(tmp_path)
    assert process.is_editor(leftover.pid)
    async with aiohttp.ClientSession() as session:
        await editor.async_start(session)
    assert await asyncio.wait_for(leftover.wait(), 5) == 0
    assert f"earlier run left behind (process {leftover.pid})" in caplog.text
    assert editor._process is not None
    assert (tmp_path / "data" / "editor.pid").read_text() == str(editor._process.pid)
    await editor.async_stop()


async def test_a_leftover_editor_that_does_not_end_is_killed(
    editor: EditorProcess, tmp_path: Path, monkeypatch
) -> None:
    leftover = await _leftover(tmp_path, FAKE_STUBBORN="1")
    monkeypatch.setattr(process, "_STOP_TIMEOUT", 0.5)
    async with aiohttp.ClientSession() as session:
        await editor.async_start(session)
    assert await asyncio.wait_for(leftover.wait(), 5) == -signal.SIGKILL
    await editor.async_stop()


async def test_a_pid_file_of_another_process_is_ignored(
    editor: EditorProcess, tmp_path: Path, caplog
) -> None:
    pid_file = tmp_path / "data" / "editor.pid"
    for content in ("not a number", str(os.getpid())):
        pid_file.write_text(content)
        async with aiohttp.ClientSession() as session:
            await editor.async_start(session)
        await editor.async_stop()
    assert "left behind" not in caplog.text
    assert not process.is_editor(os.getpid())
