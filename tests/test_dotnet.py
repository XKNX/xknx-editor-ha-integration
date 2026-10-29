"""The .NET runtime and the key-extraction helpers: what is downloaded, checked and installed."""

import hashlib
import io
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant.test_util.aiohttp import AiohttpClientMocker

from custom_components.xknx_editor import dotnet
from custom_components.xknx_editor.dotnet import (
    DotnetError,
    DotnetInstall,
    helpers_hash,
    helpers_source,
)

RUNTIME_URL = "https://example.com/dotnet-runtime-linux-musl-x64.tar.gz"


def _runtime_archive() -> bytes:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as bundle:
        for name, content, mode in (
            ("dotnet", b"#!/bin/sh\n", 0o755),
            ("LICENSE.txt", b"x", 0o644),
        ):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(content), mode
            bundle.addfile(info, io.BytesIO(content))
    return data.getvalue()


def _helpers_archive() -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        for folder in ("sign", "trace", "oauth"):
            bundle.writestr(f"{folder}/xknxkeyx.dll", b"MZ")
    return data.getvalue()


@pytest.fixture
def install(tmp_path: Path) -> DotnetInstall:
    return DotnetInstall(tmp_path / "cache")


@pytest.fixture
def runtime(aioclient_mock: AiohttpClientMocker):
    archive = _runtime_archive()
    aioclient_mock.get(RUNTIME_URL, content=archive)
    with (
        patch.object(dotnet, "runtime_id", return_value="linux-musl-x64"),
        patch.dict(
            dotnet.DOTNET_RUNTIMES,
            {"linux-musl-x64": (RUNTIME_URL, hashlib.sha512(archive).hexdigest())},
        ),
    ):
        yield aioclient_mock


def test_helpers_next_to_the_requirements() -> None:
    assert (
        helpers_source("https://github.com/x/releases/download/v1/xknx-editor-1-requirements.txt")
        == "https://github.com/x/releases/download/v1/xknx-editor-1-keyextract.zip"
    )
    assert helpers_source("/config/build/requirements.txt") == "/config/build/keyextract.zip"
    # Another name: the helpers lie in the same folder.
    assert helpers_source("/config/build/editor-reqs.lock") == "/config/build/keyextract.zip"
    digest = "ab" * 32
    assert helpers_hash(f"# keyextract.zip sha256:{digest}\nnumpy==2.5.4\n") == digest
    assert helpers_hash("numpy==2.5.4\n") is None


@pytest.mark.parametrize(
    ("machine", "musl", "expected"),
    [
        ("x86_64", True, "linux-musl-x64"),
        ("aarch64", True, "linux-musl-arm64"),
        ("x86_64", False, "linux-x64"),
        ("armv7l", True, None),
    ],
)
def test_runtime_id(machine: str, musl: bool, expected: str | None, tmp_path: Path) -> None:
    with (
        patch.object(dotnet.platform, "machine", return_value=machine),
        patch.object(dotnet.sys, "platform", "linux"),
        patch.object(dotnet.Path, "glob", return_value=iter([Path("x")] if musl else [])),
    ):
        assert dotnet.runtime_id() == expected


async def test_runtime_installed_once(
    hass: HomeAssistant, install: DotnetInstall, runtime: AiohttpClientMocker
) -> None:
    session = async_get_clientsession(hass)
    await install.async_ensure_runtime(session)
    assert (install.runtime / "dotnet").read_bytes() == b"#!/bin/sh\n"
    environment = install.environment()
    assert environment["DOTNET_ROOT"] == str(install.runtime)
    assert environment["DOTNET_SYSTEM_GLOBALIZATION_INVARIANT"] == "1"
    # Nothing is downloaded again while it is there.
    await install.async_ensure_runtime(session)
    assert runtime.call_count == 1
    # No archive is left behind.
    assert not list(install.root.glob("*.download"))


async def test_runtime_with_another_checksum_is_refused(
    hass: HomeAssistant, install: DotnetInstall, runtime: AiohttpClientMocker
) -> None:
    with (
        patch.dict(dotnet.DOTNET_RUNTIMES, {"linux-musl-x64": (RUNTIME_URL, "0" * 128)}),
        pytest.raises(DotnetError, match="expected checksum"),
    ):
        await install.async_ensure_runtime(async_get_clientsession(hass))
    assert not install.runtime.exists()
    assert install.environment() == {}
    assert not list(install.root.glob("*.download"))


async def test_a_failed_download_leaves_nothing_behind(
    hass: HomeAssistant, install: DotnetInstall, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(RUNTIME_URL, status=404)
    with (
        patch.object(dotnet, "runtime_id", return_value="linux-musl-x64"),
        patch.dict(dotnet.DOTNET_RUNTIMES, {"linux-musl-x64": (RUNTIME_URL, "0" * 128)}),
        pytest.raises(DotnetError, match=f"Could not download {RUNTIME_URL}"),
    ):
        await install.async_ensure_runtime(async_get_clientsession(hass))
    assert not install.runtime.exists()
    assert not list(install.root.glob("*.download"))


async def test_a_broken_runtime_archive_is_refused(
    hass: HomeAssistant, install: DotnetInstall, aioclient_mock: AiohttpClientMocker
) -> None:
    archive = b"not a tar archive"
    aioclient_mock.get(RUNTIME_URL, content=archive)
    with (
        patch.object(dotnet, "runtime_id", return_value="linux-musl-x64"),
        patch.dict(
            dotnet.DOTNET_RUNTIMES,
            {"linux-musl-x64": (RUNTIME_URL, hashlib.sha512(archive).hexdigest())},
        ),
        pytest.raises(DotnetError, match="Could not unpack"),
    ):
        await install.async_ensure_runtime(async_get_clientsession(hass))
    assert not install.runtime.exists()
    assert not install.runtime.with_name("dotnet.new").exists()
    assert install.environment() == {}


async def test_no_runtime_for_this_platform(hass: HomeAssistant, install: DotnetInstall) -> None:
    with (
        patch.object(dotnet, "runtime_id", return_value=None),
        pytest.raises(DotnetError, match=r"no \.NET runtime"),
    ):
        await install.async_ensure_runtime(async_get_clientsession(hass))


async def test_helpers_from_a_release(
    hass: HomeAssistant, install: DotnetInstall, aioclient_mock: AiohttpClientMocker
) -> None:
    archive = _helpers_archive()
    digest = hashlib.sha256(archive).hexdigest()
    aioclient_mock.get("https://example.com/v1/xknx-editor-1-keyextract.zip", content=archive)
    session = async_get_clientsession(hass)
    requirements = f"# keyextract.zip sha256:{digest}\nnumpy==2.5.4\n"
    source = "https://example.com/v1/xknx-editor-1-requirements.txt"
    await install.async_ensure_helpers(session, source, requirements)
    assert (install.helpers / "trace" / "xknxkeyx.dll").read_bytes() == b"MZ"
    assert install.environment() == {"XKNX_KEYEXTRACT_DIR": str(install.helpers)}
    await install.async_ensure_helpers(session, source, requirements)
    assert aioclient_mock.call_count == 1


async def test_helpers_from_a_file_are_checked(
    hass: HomeAssistant, install: DotnetInstall, tmp_path: Path
) -> None:
    archive = _helpers_archive()
    (tmp_path / "keyextract.zip").write_bytes(archive)
    source = str(tmp_path / "requirements.txt")
    session = async_get_clientsession(hass)
    with pytest.raises(DotnetError, match="Could not read"):
        await install.async_ensure_helpers(
            session,
            str(tmp_path / "elsewhere" / "requirements.txt"),
            "# keyextract.zip sha256:" + "0" * 64,
        )
    with pytest.raises(DotnetError, match="expected checksum"):
        await install.async_ensure_helpers(session, source, "# keyextract.zip sha256:" + "0" * 64)
    with pytest.raises(DotnetError, match="names no key-extraction helpers"):
        await install.async_ensure_helpers(session, source, "numpy==2.5.4\n")
    digest = hashlib.sha256(archive).hexdigest()
    await install.async_ensure_helpers(session, source, f"# keyextract.zip sha256:{digest}\n")
    assert (install.helpers / "sign" / "xknxkeyx.dll").exists()


async def test_helpers_with_an_entry_outside_are_refused(
    hass: HomeAssistant, install: DotnetInstall, tmp_path: Path
) -> None:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        bundle.writestr("../outside.txt", b"x")
    (tmp_path / "keyextract.zip").write_bytes(data.getvalue())
    digest = hashlib.sha256(data.getvalue()).hexdigest()
    with pytest.raises(DotnetError, match="entry outside"):
        await install.async_ensure_helpers(
            async_get_clientsession(hass),
            str(tmp_path / "requirements.txt"),
            f"# keyextract.zip sha256:{digest}\n",
        )
    assert not (tmp_path / "outside.txt").exists()
