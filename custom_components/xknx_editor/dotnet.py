"""The .NET runtime and the editor's key-extraction helpers.

The editor reads keys from the KNX authoring tool's assemblies the user uploads (the signing key,
the project-log key, the MyKNX sign-in) by running small helpers of its own on a .NET runtime. Home
Assistant has none, so the integration installs Microsoft's runtime for this platform (a pinned
version, checked by its SHA-512) next to the editor's environment, and the helpers of the editor
release (checked by the SHA-256 the release's requirements file names). Without them the editor
runs as well, only those extractions are not available.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import DOTNET_RUNTIMES, DOTNET_VERSION, HELPERS_NAME

_LOGGER = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = ClientTimeout(total=600)
_CHUNK = 1 << 20
_HELPERS_HASH = re.compile(rf"^#\s*{re.escape(HELPERS_NAME)}\s+sha256:([0-9a-f]{{64}})\s*$", re.M)
_ARCHITECTURES = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}


class DotnetError(Exception):
    """The runtime or the helpers could not be installed."""


def runtime_id() -> str | None:
    """Microsoft's name of this platform (e.g. ``linux-musl-x64``), None where .NET has none."""
    architecture = _ARCHITECTURES.get(platform.machine().lower())
    if architecture is None or not sys.platform.startswith("linux"):
        return None
    # Home Assistant's image is Alpine Linux (musl).
    musl = any(Path("/lib").glob("ld-musl-*.so.1"))
    return f"linux-{'musl-' if musl else ''}{architecture}"


def helpers_source(requirements_source: str) -> str:
    """Where the helpers of the editor release at ``requirements_source`` are."""
    if requirements_source.endswith("requirements.txt"):
        return requirements_source[: -len("requirements.txt")] + HELPERS_NAME
    return requirements_source.rsplit("/", 1)[0] + "/" + HELPERS_NAME


def helpers_hash(requirements: str) -> str | None:
    """The SHA-256 of the helpers the requirements file names, if it names one."""
    match = _HELPERS_HASH.search(requirements)
    return match.group(1) if match else None


class DotnetInstall:
    """The runtime in ``root``/dotnet and the helpers in ``root``/keyextract."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.runtime = root / "dotnet"
        self.helpers = root / "keyextract"
        self._state_file = root / "dotnet.json"

    def state(self) -> dict[str, str]:
        """What is installed (see the async_ensure methods)."""
        try:
            state = json.loads(self._state_file.read_text(encoding="utf-8"))
        except OSError, ValueError:
            return {}
        return state if isinstance(state, dict) else {}

    def _write_state(self, **values: str) -> None:
        self._state_file.write_text(json.dumps(self.state() | values), encoding="utf-8")

    def environment(self) -> dict[str, str]:
        """The settings the editor finds the runtime and the helpers with (as far as installed)."""
        state = self.state()
        env: dict[str, str] = {}
        if state.get("runtime") and (self.runtime / "dotnet").exists():
            env.update(
                DOTNET_ROOT=str(self.runtime),
                DOTNET_SYSTEM_GLOBALIZATION_INVARIANT="1",
                DOTNET_CLI_TELEMETRY_OPTOUT="1",
                DOTNET_NOLOGO="1",
            )
        if state.get("helpers") and self.helpers.is_dir():
            env["XKNX_KEYEXTRACT_DIR"] = str(self.helpers)
        return env

    async def async_ensure_runtime(self, session: ClientSession) -> None:
        """Install the runtime of DOTNET_VERSION, unless it is there already."""
        # It looks for the C library in /lib: a file-system call.
        rid = await asyncio.to_thread(runtime_id)
        if rid is None or rid not in DOTNET_RUNTIMES:
            raise DotnetError(f"no .NET runtime for {platform.machine()}")
        wanted = f"{DOTNET_VERSION} {rid}"
        state = await asyncio.to_thread(self.state)
        if state.get("runtime") == wanted and await asyncio.to_thread(
            (self.runtime / "dotnet").exists
        ):
            return
        url, sha512 = DOTNET_RUNTIMES[rid]
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)
        archive = await _download(session, url, self.root, hashlib.sha512(), sha512)
        try:
            await asyncio.to_thread(_unpack, archive, self.runtime, tar=True)
        finally:
            await asyncio.to_thread(archive.unlink, missing_ok=True)
        await asyncio.to_thread(self._write_state, runtime=wanted)

    async def async_ensure_helpers(
        self, session: ClientSession, requirements_source: str, requirements: str
    ) -> None:
        """Install the helpers of the editor release whose requirements file is
        ``requirements_source`` (with the text ``requirements``), unless they are there."""
        sha256 = helpers_hash(requirements)
        if sha256 is None:
            raise DotnetError("the editor release names no key-extraction helpers")
        state = await asyncio.to_thread(self.state)
        if state.get("helpers") == sha256 and await asyncio.to_thread(self.helpers.is_dir):
            return
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)
        source = helpers_source(requirements_source)
        if source.startswith(("http://", "https://")):
            archive = await _download(session, source, self.root, hashlib.sha256(), sha256)
        else:
            archive = await asyncio.to_thread(_copy_checked, Path(source), self.root, sha256)
        try:
            await asyncio.to_thread(_unpack, archive, self.helpers, tar=False)
        finally:
            await asyncio.to_thread(archive.unlink, missing_ok=True)
        await asyncio.to_thread(self._write_state, helpers=sha256)


async def _download(
    session: ClientSession, url: str, folder: Path, digest: hashlib._Hash, expected: str
) -> Path:
    """``url`` as a file in ``folder``, checked against ``expected`` (hex of ``digest``)."""
    handle, name = await asyncio.to_thread(tempfile.mkstemp, dir=folder, suffix=".download")
    path = Path(name)
    try:
        with os.fdopen(handle, "wb") as file:
            async with session.get(url, timeout=_DOWNLOAD_TIMEOUT) as response:
                response.raise_for_status()
                async for chunk in response.content.iter_chunked(_CHUNK):
                    digest.update(chunk)
                    await asyncio.to_thread(file.write, chunk)
    except (ClientError, TimeoutError, OSError) as error:
        await asyncio.to_thread(path.unlink, missing_ok=True)
        raise DotnetError(f"Could not download {url}: {error}") from error
    if digest.hexdigest() != expected:
        await asyncio.to_thread(path.unlink, missing_ok=True)
        raise DotnetError(f"{url} does not have the expected checksum")
    return path


def _copy_checked(source: Path, folder: Path, sha256: str) -> Path:
    try:
        data = source.read_bytes()
    except OSError as error:
        raise DotnetError(f"Could not read {source}: {error}") from error
    if hashlib.sha256(data).hexdigest() != sha256:
        raise DotnetError(f"{source} does not have the expected checksum")
    handle, name = tempfile.mkstemp(dir=folder, suffix=".download")
    with os.fdopen(handle, "wb") as file:
        file.write(data)
    return Path(name)


def _unpack(archive: Path, target: Path, *, tar: bool) -> None:
    """Unpack ``archive`` into ``target`` in place of what was there."""
    staging = target.with_name(target.name + ".new")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        if tar:
            with tarfile.open(archive) as bundle:
                bundle.extractall(staging, filter="data")
        else:
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.namelist():
                    resolved = (staging / member).resolve()
                    if not resolved.is_relative_to(staging.resolve()):
                        raise DotnetError(f"{archive.name} has an entry outside: {member}")
                bundle.extractall(staging)
    except (tarfile.TarError, zipfile.BadZipFile, OSError) as error:
        shutil.rmtree(staging, ignore_errors=True)
        raise DotnetError(f"Could not unpack {archive.name}: {error}") from error
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
