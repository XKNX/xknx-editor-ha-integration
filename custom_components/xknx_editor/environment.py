"""The editor's own Python environment next to Home Assistant's.

The editor runs as a process of its own, so it has its own environment: it gets exactly the
package versions it was released with, and Home Assistant's packages stay as they are. The
environment uses Home Assistant's Python and is created and filled with uv, which Home Assistant
itself installs packages with. When Home Assistant moves to another Python version, the
environment is created again. An environment whose packages cannot be imported (an install cut
off by a restart can leave a package half written) is created again as well.
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


# What the editor imports first: a damaged install of these shows here, not as the editor ending.
_CHECK = "import numpy; numpy.dtype; import imgui_bundle; import editor_gui.web.frames"
_CHECK_TIMEOUT = 120
_FOREIGN_PYTHON_SETTINGS = ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV")


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
            if (problem := await _check(self.python)) is None:
                return self.python
            _LOGGER.warning("The editor's environment is damaged, creating it again: %s", problem)
            return await self._async_install(requirements, wanted, progress, create=True)
        create = not exists or state.get("python") != wanted["python"]
        python = await self._async_install(requirements, wanted, progress, create=create)
        if create or (problem := await _check(python)) is None:
            return python
        _LOGGER.warning("The editor's environment is damaged, creating it again: %s", problem)
        return await self._async_install(requirements, wanted, progress, create=True)

    async def _async_install(
        self,
        requirements: str,
        wanted: dict[str, str],
        progress: Callable[[str], None] | None,
        *,
        create: bool,
    ) -> Path:
        """Install ``requirements`` (into a new environment with ``create``) and record
        ``wanted``. A new environment whose packages still cannot be imported is an error."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)
        if create:
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
        if create and (problem := await _check(self.python)) is not None:
            raise InstallError(f"The editor's packages cannot be imported: {problem}")
        await asyncio.to_thread(self._write_state, wanted)
        return self.python


async def _check(python: Path) -> str | None:
    """Why the packages of the environment of ``python`` cannot be imported (the end of the
    error), or None when they can."""
    try:
        process = await asyncio.create_subprocess_exec(
            str(python),
            "-c",
            _CHECK,
            # As the editor's process gets it: without Home Assistant's own Python settings.
            env={k: v for k, v in os.environ.items() if k not in _FOREIGN_PYTHON_SETTINGS},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as error:
        return str(error)
    try:
        async with asyncio.timeout(_CHECK_TIMEOUT):
            output, _ = await process.communicate()
    except TimeoutError:
        process.kill()
        await process.wait()
        return f"importing took longer than {_CHECK_TIMEOUT} s"
    if process.returncode:
        lines = output.decode("utf-8", "replace").strip().splitlines()
        return lines[-1] if lines else f"exit code {process.returncode}"
    return None


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
