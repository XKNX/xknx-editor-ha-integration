"""The files a release of the integration installs the editor from (scripts/release_files.py)."""

import hashlib
import importlib.util
import json
import sys
import zipfile
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


def test_the_wheels_go_into_a_relative_out_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = tmp_path / "editor"
    for member in ("packages/proj", "apps/editor-gui"):
        (editor / member).mkdir(parents=True)
        (editor / member / "pyproject.toml").write_text("")
    wheels = {
        "proj": "xknxeditor_proj-0.9.0-py3-none-any.whl",
        "editor-gui": "xknxeditor_gui-0.9.0-py3-none-any.whl",
        "xknxproject": "xknxproject-3.10.0-py3-none-any.whl",
    }

    def run(*args: str, cwd: Path) -> str:
        # Like uv: a relative --out-dir is taken from the folder it runs in.
        if args[:2] == ("uv", "build"):
            (cwd / args[4]).mkdir(parents=True, exist_ok=True)
            (cwd / args[4] / wheels[Path(args[5]).name]).write_bytes(args[5].encode())
        return _EXPORTED if args[:2] == ("uv", "export") else ""

    monkeypatch.setattr(release_files, "_run", run)
    monkeypatch.setattr(release_files, "build_helpers", lambda _e, target: target.write_bytes(b"z"))
    integration = tmp_path / "integration"
    integration.mkdir()
    monkeypatch.chdir(integration)

    requirements = release_files.build(editor, "https://example.com/0.9.0", Path("release"))

    assert sorted(p.name for p in (integration / "release").glob("*.whl")) == sorted(
        wheels.values()
    )
    text = requirements.read_text()
    assert (
        "xknxeditor-gui @ https://example.com/0.9.0/xknxeditor_gui-0.9.0-py3-none-any.whl" in text
    )
    assert not list(editor.rglob("*.whl"))


def test_run_returns_the_output(tmp_path: Path) -> None:
    assert release_files._run(sys.executable, "-c", "print('built')", cwd=tmp_path) == "built\n"


def test_the_helpers_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    editor = tmp_path / "editor"
    sources = editor / release_files._SOURCES
    sources.mkdir(parents=True)
    for _name, source, _package in release_files.HELPERS:
        (sources / source).write_text(f"// {source}")
    projects: dict[str, str] = {}

    def run(*args: str, cwd: Path) -> str:
        # dotnet publish: the project's assembly, and a symbol file the archive leaves out
        assert args[:2] == ("dotnet", "publish")
        projects[cwd.name] = (cwd / "xknxkeyx.csproj").read_text()
        out = Path(args[args.index("-o") + 1])
        out.mkdir(parents=True)
        (out / "xknxkeyx.dll").write_bytes(b"MZ")
        (out / "xknxkeyx.pdb").write_bytes(b"symbols")
        return ""

    monkeypatch.setattr(release_files, "_run", run)
    target = tmp_path / "keyextract.zip"
    release_files.build_helpers(editor, target)

    with zipfile.ZipFile(target) as archive:
        assert sorted(archive.namelist()) == [
            "oauth/xknxkeyx.dll",
            "sign/xknxkeyx.dll",
            "trace/xknxkeyx.dll",
        ]
    # Each helper from its own source; only the trace helper needs a package.
    assert '<Compile Include="_extract_trace.cs" />' in projects["trace"]
    assert "System.Configuration.ConfigurationManager" in projects["trace"]
    assert "PackageReference" not in projects["sign"]
    assert "<TargetFramework>net10.0</TargetFramework>" in projects["oauth"]


@pytest.mark.parametrize(
    ("where", "base_url"),
    [
        (["--tag", "0.9.0"], f"{release_files.RELEASES_URL}/0.9.0"),
        (["--base-url", "file:///builds/editor"], "file:///builds/editor"),
    ],
)
def test_the_command_line(
    where: list[str],
    base_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[Path, str, Path]] = []

    def build(editor: Path, url: str, out: Path) -> Path:
        calls.append((editor, url, out))
        return out / "xknx-editor-0.9.0-requirements.txt"

    monkeypatch.setattr(release_files, "build", build)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["release_files.py", "--editor", "editor", *where, "--out", "release"]
    )
    release_files.main()
    assert calls == [(tmp_path / "editor", base_url, Path("release"))]
    assert "xknx-editor-0.9.0-requirements.txt and 0 wheels" in capsys.readouterr().out


def test_no_requirements_file_without_the_editor_wheel(tmp_path: Path) -> None:
    wheel = tmp_path / "xknxproject-3.10.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")

    with pytest.raises(SystemExit, match="the editor itself would not be installed"):
        release_files.requirements_text(_EXPORTED, [wheel], "https://example.com", "ab" * 32)


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
