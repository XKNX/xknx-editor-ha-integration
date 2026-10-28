"""The files a release of the integration installs the editor from (scripts/release_files.py)."""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry

from custom_components.xknx_editor.const import (
    CONF_EDITOR_PASSWORD,
    DOMAIN,
    EDITOR_VERSION,
    REQUIREMENTS_URL,
)
from custom_components.xknx_editor.dotnet import helpers_hash, helpers_source
from custom_components.xknx_editor.manager import EditorManager

_ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_files", _ROOT / "scripts" / "release_files.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_files = _load()

_EXPORTED = """\
aiofile==3.12.3 \\
    --hash=sha256:5c1b \\
    --hash=sha256:caa6
    # via py-key-value-aio
xknxproject @ git+https://github.com/XKNX/xknxproject@f97db068884eca2b460abb1fb4db2262345f7f47
    # via xknxeditor-proj
zipp==3.23.0 \\
    --hash=sha256:0718
"""


def test_the_editor_version_is_the_one_the_integration_installs() -> None:
    const = (_ROOT / "custom_components" / "xknx_editor" / "const.py").read_text()
    assert release_files.editor_version(const) == EDITOR_VERSION
    with pytest.raises(SystemExit, match="EDITOR_VERSION not found"):
        release_files.editor_version("")


def test_packages_from_git() -> None:
    assert release_files.git_sources(_EXPORTED) == [
        (
            "xknxproject",
            "https://github.com/XKNX/xknxproject",
            "f97db068884eca2b460abb1fb4db2262345f7f47",
        )
    ]


def test_the_requirements_file(tmp_path: Path) -> None:
    wheels = []
    for name in ("xknxproject-3.10.0-py3-none-any.whl", "xknxeditor_gui-0.9.0-py3-none-any.whl"):
        wheel = tmp_path / name
        wheel.write_bytes(name.encode())
        wheels.append(wheel)
    base = f"{release_files.RELEASES_URL}/0.2.0"

    text = release_files.requirements_text(_EXPORTED, wheels, base, "ab" * 32)
    lines = text.splitlines()

    # The integration finds the helpers by the hash this file names.
    assert helpers_hash(text) == "ab" * 32
    # The packages from PyPI stay as exported, with their hashes.
    assert "aiofile==3.12.3 \\" in lines
    assert "zipp==3.23.0 \\" in lines
    # The git revision is replaced by its wheel, together with its indented lines.
    assert not any("git+" in line for line in lines)
    assert "    # via xknxeditor-proj" not in lines
    gui = hashlib.sha256(b"xknxeditor_gui-0.9.0-py3-none-any.whl").hexdigest()
    assert (
        f"xknxeditor-gui @ {base}/xknxeditor_gui-0.9.0-py3-none-any.whl \\\n    --hash=sha256:{gui}"
    ) in text
    assert f"xknxproject @ {base}/xknxproject-3.10.0-py3-none-any.whl \\" in lines
    assert text.endswith("\n")


def test_the_files_are_where_the_integration_looks_for_them() -> None:
    url = REQUIREMENTS_URL.format(release="0.2.0", version="0.9.0")
    assert url == f"{release_files.RELEASES_URL}/0.2.0/xknx-editor-0.9.0-requirements.txt"
    assert helpers_source(url) == (
        f"{release_files.RELEASES_URL}/0.2.0/xknx-editor-0.9.0-{release_files.HELPERS_NAME}"
    )


async def test_the_release_of_this_version_is_installed(hass: HomeAssistant) -> None:
    async def run(self: EditorManager) -> None:
        pass

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_EDITOR_PASSWORD: "secret"})
    entry.add_to_hass(hass)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(EditorManager, "async_run", run)
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
    manifest = json.loads(
        (_ROOT / "custom_components" / "xknx_editor" / "manifest.json").read_text()
    )
    assert entry.runtime_data._requirements == REQUIREMENTS_URL.format(
        release=manifest["version"], version=EDITOR_VERSION
    )
    await entry.runtime_data.client.close()
