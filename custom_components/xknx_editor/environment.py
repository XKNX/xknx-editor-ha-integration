"""The editor's own Python environment next to Home Assistant's.

The editor runs as a process of its own, so it has its own environment: it gets exactly the
package versions it was released with, and Home Assistant's packages stay as they are. The
environment uses Home Assistant's Python and is created and filled with uv, which Home Assistant
itself installs packages with. When Home Assistant moves to another Python version, the
environment is created again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from aiohttp import ClientError, ClientSession, ClientTimeout

_LOGGER = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = ClientTimeout(total=120)
# Settings of Home Assistant's own package installs that must not reach ours. Its image adds its
# own wheel index, which uv would prefer to PyPI: builds of the same versions with other hashes
# than the locked ones.
_FOREIGN_UV_SETTINGS = (
    "UV_SYSTEM_PYTHON",
    "UV_PYTHON",
    "UV_PROJECT_ENVIRONMENT",
    "UV_INDEX",
    "UV_INDEX_URL",
    "UV_DEFAULT_INDEX",
    "UV_EXTRA_INDEX_URL",
    "UV_FIND_LINKS",
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "VIRTUAL_ENV",
)


class InstallError(Exception):
    """The environment could not be created or filled."""


def python_tag() -> str:
    """The Python the environment is created with: its version (major.minor) and path."""
    return f"{sys.version_info.major}.{sys.version_info.minor} {os.path.realpath(sys.executable)}"


class EditorEnvironment:
    """The environment in ``root``: the virtual environment, its requirements and its state."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.venv = root / "env"
        self._state_file = root / "state.json"
        self._requirements_file = root / "requirements.txt"

    @property
    def python(self) -> Path:
        """The environment's Python."""
        if sys.platform == "win32":
            return self.venv / "Scripts" / "python.exe"
        return self.venv / "bin" / "python"

    def state(self) -> dict[str, str]:
        """What the environment was last made for (see async_ensure)."""
        try:
            state = json.loads(self._state_file.read_text(encoding="utf-8"))
        except OSError, ValueError:
            return {}
        return state if isinstance(state, dict) else {}

    def _write_state(self, state: dict[str, str]) -> None:
        self._state_file.write_text(json.dumps(state), encoding="utf-8")

    async def async_requirements(self, session: ClientSession, source: str) -> str:
        """The requirements file at ``source``: a URL, or a path on this host."""
        if source.startswith(("http://", "https://")):
            try:
                async with session.get(source, timeout=_DOWNLOAD_TIMEOUT) as response:
                    response.raise_for_status()
                    return await response.text()
            except (ClientError, TimeoutError) as error:
                raise InstallError(f"Could not download {source}: {error}") from error
        try:
            return await asyncio.to_thread(Path(source).read_text, encoding="utf-8")
        except OSError as error:
            raise InstallError(f"Could not read {source}: {error}") from error

    async def async_ensure(
        self, requirements: str, progress: Callable[[str], None] | None = None
    ) -> Path:
        """Make the environment match ``requirements`` (the text of a requirements file), and
        return its Python. Nothing is done when it already matches."""
        wanted = {
            "python": python_tag(),
            "requirements": hashlib.sha256(requirements.encode("utf-8")).hexdigest(),
        }
        state = await asyncio.to_thread(self.state)
        exists = await asyncio.to_thread(self.python.exists)
        if exists and state == wanted:
            return self.python
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)
        if not exists or state.get("python") != wanted["python"]:
            if progress:
                progress("Creating the environment")
            await _uv("venv", "--clear", "--python", sys.executable, str(self.venv))
            # Created: a failed install below is tried again in this environment.
            await asyncio.to_thread(self._write_state, {"python": wanted["python"]})
        await asyncio.to_thread(self._requirements_file.write_text, requirements, encoding="utf-8")
        if progress:
            progress("Installing the packages")
        await _uv(
            "pip",
            # Exactly these packages: what an earlier release needed and this one does not goes.
            "sync",
            "--python",
            str(self.python),
            # Every package comes as a wheel: nothing is compiled on this host.
            "--no-build",
            "--no-cache",
            str(self._requirements_file),
        )
        await asyncio.to_thread(self._write_state, wanted)
        return self.python


async def _uv(*args: str) -> None:
    """Run uv (the one Home Assistant installs its packages with) with ``args``."""
    env = {k: v for k, v in os.environ.items() if k not in _FOREIGN_UV_SETTINGS}
    _LOGGER.debug("Running uv %s", " ".join(args))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uv",
        *args,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    if process.returncode:
        text = output.decode("utf-8", "replace").strip()
        raise InstallError(f"uv {args[0]} failed: {text[-2000:]}")
