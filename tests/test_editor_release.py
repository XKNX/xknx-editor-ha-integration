"""An integration release for each new editor release (scripts/editor_release.py)."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from custom_components.xknx_editor.const import EDITOR_VERSION

_ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "editor_release", _ROOT / "scripts" / "editor_release.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


editor_release = _load()

_CONST = (
    'DOMAIN: Final = "xknx_editor"\n\nEDITOR_VERSION: Final = "0.9.0"\nOTHER: Final = "0.9.0"\n'
)


def test_a_newer_editor_release_is_found() -> None:
    assert editor_release.newer_editor_version("xknx-editor-v0.10.0", _CONST) == "0.10.0"
    assert editor_release.newer_editor_version("xknx-editor-v0.9.1", _CONST) == "0.9.1"
    # Compared by number, not as text: 0.10.0 is newer than 0.9.0, 0.8.9 older.
    assert editor_release.newer_editor_version("xknx-editor-v0.8.9", _CONST) is None


def test_the_installed_editor_release_needs_no_new_release() -> None:
    assert editor_release.newer_editor_version("xknx-editor-v0.9.0", _CONST) is None


def test_an_integration_only_release_does_not_hide_a_newer_editor() -> None:
    # The integration 0.9.0.1 still installs editor 0.9.0: EDITOR_VERSION decides.
    assert editor_release.newer_editor_version("xknx-editor-v0.9.1", _CONST) == "0.9.1"


@pytest.mark.parametrize("tag", ["v0.10.0", "xknx-editor-v0.10", "xknx-editor-v0.10.0rc1"])
def test_other_tags_are_refused(tag: str) -> None:
    with pytest.raises(SystemExit):
        editor_release.newer_editor_version(tag, _CONST)


def test_the_repository_names_the_editor_version() -> None:
    const = (_ROOT / "custom_components" / "xknx_editor" / "const.py").read_text(encoding="utf-8")
    assert editor_release.editor_version(const) == EDITOR_VERSION


def test_the_bump_changes_only_the_versions() -> None:
    const = editor_release.bumped_const(_CONST, "0.10.0")
    assert const == _CONST.replace(
        'EDITOR_VERSION: Final = "0.9.0"', 'EDITOR_VERSION: Final = "0.10.0"'
    )
    manifest = (_ROOT / "custom_components" / "xknx_editor" / "manifest.json").read_text(
        encoding="utf-8"
    )
    bumped = editor_release.bumped_manifest(manifest, "0.10.0")
    assert json.loads(bumped) == {**json.loads(manifest), "version": "0.10.0"}
    # The layout stays: only the version line differs.
    differing = [
        (a, b) for a, b in zip(manifest.splitlines(), bumped.splitlines(), strict=True) if a != b
    ]
    assert differing == [
        (f'  "version": "{json.loads(manifest)["version"]}"', '  "version": "0.10.0"')
    ]
    bug_report = (_ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml").read_text(
        encoding="utf-8"
    )
    bumped = editor_release.bumped_bug_report(bug_report, "0.10.0")
    assert 'label: Integration Version\n      placeholder: "0.10.0"' in bumped
    # The Home Assistant version's placeholder stays.
    assert bumped.count("placeholder:") == bug_report.count("placeholder:")
    assert 'placeholder: "2026.11.0"' in bumped


def test_a_bump_without_its_lines_is_refused() -> None:
    with pytest.raises(SystemExit):
        editor_release.bumped_const("DOMAIN = 1\n", "0.10.0")
    with pytest.raises(SystemExit):
        editor_release.bumped_manifest('{\n  "domain": "x"\n}\n', "0.10.0")
    with pytest.raises(SystemExit):
        editor_release.bumped_bug_report("body: []\n", "0.10.0")


def test_bump_writes_all_three_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = {}
    for name, source in (
        ("CONST", _ROOT / "custom_components" / "xknx_editor" / "const.py"),
        ("MANIFEST", _ROOT / "custom_components" / "xknx_editor" / "manifest.json"),
        ("BUG_REPORT", _ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"),
    ):
        copy = tmp_path / source.name
        copy.write_bytes(source.read_bytes())
        monkeypatch.setattr(editor_release, name, copy)
        files[name] = copy
    editor_release.bump("0.10.0")
    assert editor_release.editor_version(files["CONST"].read_text(encoding="utf-8")) == "0.10.0"
    assert json.loads(files["MANIFEST"].read_text(encoding="utf-8"))["version"] == "0.10.0"
    assert 'placeholder: "0.10.0"' in files["BUG_REPORT"].read_text(encoding="utf-8")
    assert b"\r\n" not in files["CONST"].read_bytes()


def test_the_release_notes_name_the_editor_release_and_link_its_numbers() -> None:
    editor_notes = (
        "## New\n\n"
        "- **Integration:** runs in the sidebar (#171).\n"
        "- Codepage (#172), see XKNX/xknx-editor#170 and https://example.org/page#5.\n"
    )
    notes = editor_release.release_notes("0.10.0", editor_notes)
    assert notes.startswith(
        "Installs [XKNX Editor 0.10.0]"
        "(https://github.com/XKNX/xknx-editor/releases/tag/xknx-editor-v0.10.0).\n"
    )
    assert "## XKNX Editor 0.10.0\n\n### New\n" in notes
    assert "(XKNX/xknx-editor#171)" in notes
    assert "(XKNX/xknx-editor#172)" in notes
    # Already linked, or part of a URL: unchanged.
    assert "see XKNX/xknx-editor#170 and https://example.org/page#5." in notes


def test_release_notes_without_editor_notes() -> None:
    notes = editor_release.release_notes("0.10.0", "\n")
    assert notes == (
        "Installs [XKNX Editor 0.10.0]"
        "(https://github.com/XKNX/xknx-editor/releases/tag/xknx-editor-v0.10.0).\n"
    )


def test_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    const = tmp_path / "const.py"
    const.write_text(_CONST, encoding="utf-8")
    monkeypatch.setattr(editor_release, "CONST", const)
    for tag, printed in (("xknx-editor-v0.10.0", "0.10.0\n"), ("xknx-editor-v0.9.0", "")):
        monkeypatch.setattr(sys, "argv", ["editor_release.py", "check", tag])
        editor_release.main()
        assert capsys.readouterr().out == printed
    notes = tmp_path / "notes.md"
    notes.write_text("Fixed (#1).\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["editor_release.py", "notes", "0.10.0", str(notes)])
    editor_release.main()
    assert "Fixed (XKNX/xknx-editor#1)." in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["editor_release.py", "bump", "latest"])
    with pytest.raises(SystemExit):
        editor_release.main()
    bumped: list[str] = []
    monkeypatch.setattr(editor_release, "bump", bumped.append)
    monkeypatch.setattr(sys, "argv", ["editor_release.py", "bump", "0.10.0"])
    editor_release.main()
    assert bumped == ["0.10.0"]
