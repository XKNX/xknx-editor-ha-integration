"""The editor process: its web UI on a port of this host, reachable for the proxy only."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import socket
from collections import deque
from pathlib import Path

from aiohttp import ClientError, ClientSession, ClientTimeout

_LOGGER = logging.getLogger(__name__)

HOST = "127.0.0.1"
# Waiting for the web UI to answer after the start.
_START_TIMEOUT = 180.0
_STOP_TIMEOUT = 30.0
_PROBE_TIMEOUT = ClientTimeout(total=5)
# Settings of Home Assistant's process that must not reach the editor's.
_FOREIGN_SETTINGS = (
    "PYTHONHOME",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "UV_SYSTEM_PYTHON",
    "UV_PYTHON",
)
# The editor's log colors (terminal escape sequences), left out of Home Assistant's log.
_COLORS = re.compile(r"\x1b\[[0-9;]*m")


class StartError(Exception):
    """The editor did not start."""


def free_port() -> int:
    """A port of HOST nothing listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return int(probe.getsockname()[1])


class EditorProcess:
    """One run of the editor with the Python of its environment.

    ``data`` holds the editor's settings and catalog, ``files`` is the folder its file dialogs
    work in, ``cache`` its downloads. Browsers never reach the editor directly: it accepts only
    signed-in sessions, and only the proxy knows ``password``. Its MCP server runs on a port of
    its own, with ``mcp_token`` that only the proxy knows; ``mcp_url`` is the address MCP clients
    use through Home Assistant, which the editor shows.
    """

    def __init__(
        self,
        python: Path,
        *,
        data: Path,
        files: Path,
        cache: Path,
        password: str,
        mcp_token: str,
        mcp_url: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        self._python = python
        self._data = data
        self._files = files
        self._cache = cache
        self._password = password
        self.mcp_token = mcp_token
        self._mcp_url = mcp_url
        # More settings for the editor: the .NET runtime and its helpers (dotnet.py).
        self._extra_environment = environment or {}
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        # The last lines the editor wrote, for the message when it fails.
        self.output: deque[str] = deque(maxlen=40)
        self.port = 0
        self.mcp_port = 0

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/"

    @property
    def mcp_endpoint(self) -> str:
        """The editor's MCP endpoint."""
        return f"http://{HOST}:{self.mcp_port}/mcp"

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def _environment(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in _FOREIGN_SETTINGS}
        env.update(
            XDG_DATA_HOME=str(self._data / "data"),
            XDG_CONFIG_HOME=str(self._data / "config"),
            XDG_STATE_HOME=str(self._data / "state"),
            XDG_CACHE_HOME=str(self._cache),
            XKNX_WEB_PASSWORD=self._password,
            XKNX_MCP_AUTH_TOKEN=self.mcp_token,
            PYTHONUNBUFFERED="1",
            NO_COLOR="1",
        )
        env.update(self._extra_environment)
        if dotnet := self._extra_environment.get("DOTNET_ROOT"):
            # The editor runs the .NET runtime as `dotnet`.
            env["PATH"] = os.pathsep.join(filter(None, (dotnet, env.get("PATH"))))
        return env

    async def async_start(self, session: ClientSession) -> None:
        """Start the editor and wait until its web UI answers."""
        for folder in (self._data, self._files, self._cache):
            await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        self.port = free_port()
        self.mcp_port = free_port()
        self.output.clear()
        mcp_url = [f"--mcp-url={self._mcp_url}"] if self._mcp_url else []
        self._process = await asyncio.create_subprocess_exec(
            str(self._python),
            "-m",
            "editor_gui.main",
            f"--web={HOST}:{self.port}",
            f"--web-proxy-ip={HOST}",
            f"--web-files={self._files}",
            "--web-no-browser",
            "--web-no-tray",
            # Inside Home Assistant: a new version is only announced (Home Assistant updates the
            # editor with the integration).
            "--home-assistant",
            # The MCP server, reachable through Home Assistant only (proxy.McpProxyView).
            f"--mcp={HOST}:{self.mcp_port}",
            *mcp_url,
            env=self._environment(),
            cwd=str(self._data),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._reader = asyncio.create_task(self._read_output(self._process))
        try:
            async with asyncio.timeout(_START_TIMEOUT):
                while not await self._answers(session):
                    if not self.running:
                        raise StartError(self._failure("The editor ended"))
                    await asyncio.sleep(0.5)
        except TimeoutError:
            await self.async_stop()
            raise StartError(self._failure("The editor did not answer")) from None
        _LOGGER.info("XKNX Editor runs on port %s", self.port)

    async def _answers(self, session: ClientSession) -> bool:
        try:
            async with session.get(self.url, timeout=_PROBE_TIMEOUT) as response:
                return response.status == 200
        except ClientError, TimeoutError:
            return False

    def _failure(self, what: str) -> str:
        lines = "\n".join(self.output)
        return f"{what}:\n{lines}" if lines else what

    async def _read_output(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        async for raw in process.stdout:
            line = _COLORS.sub("", raw.decode("utf-8", "replace")).rstrip()
            if not line:
                continue
            self.output.append(line)
            if "[error" in line or "Traceback" in line:
                _LOGGER.warning("%s", line)
            else:
                _LOGGER.debug("%s", line)

    async def async_wait(self) -> int:
        """Wait until the editor ends; its exit code."""
        assert self._process is not None
        code = await self._process.wait()
        if self._reader is not None:
            await self._reader
        return code

    async def async_stop(self) -> None:
        """End the editor like File > Exit; kill it when it does not end in time."""
        process = self._process
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            async with asyncio.timeout(_STOP_TIMEOUT):
                await process.wait()
        except TimeoutError:
            _LOGGER.warning("XKNX Editor did not end in time, killing it")
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        if self._reader is not None:
            await self._reader
