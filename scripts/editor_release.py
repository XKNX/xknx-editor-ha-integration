"""Follow the editor's releases: an integration release for each new editor release.

    python scripts/editor_release.py check xknx-editor-v0.10.0     # prints 0.10.0, or nothing
    python scripts/editor_release.py bump 0.10.0
    python scripts/editor_release.py notes 0.10.0 editor-notes.md  # the release notes

``.github/workflows/editor-release.yml`` runs these when the editor has a release newer than the
one ``EDITOR_VERSION`` in const.py names: ``bump`` sets the version the integration installs and
its own version (the same one, see AGENTS.md), and ``notes`` writes the integration's release
notes from the editor's. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONST = ROOT / "custom_components" / "xknx_editor" / "const.py"
MANIFEST = ROOT / "custom_components" / "xknx_editor" / "manifest.json"
BUG_REPORT = ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
EDITOR_REPO = "XKNX/xknx-editor"
EDITOR_TAG_PREFIX = "xknx-editor-v"

_EDITOR_VERSION = re.compile(r'^(EDITOR_VERSION: Final = ")([^"]+)(")$', re.M)
_MANIFEST_VERSION = re.compile(r'^(  "version": ")([^"]+)(")', re.M)
_PLACEHOLDER = re.compile(r'(label: Integration Version\n\s+placeholder: ")([^"]+)(")')
_VERSION = re.compile(r"^\d+(\.\d+){2}$")
# "(#171)" or "#171" not already preceded by a repository or a word
_ISSUE = re.compile(r"(?<![\w/])#(\d+)\b")


def version_key(version: str) -> tuple[int, ...]:
    """``version`` ("0.9.0", "0.9.0.1") for comparing."""
    return tuple(int(part) for part in version.split("."))


def editor_version(const: str) -> str:
    """The editor release the integration installs, from the text of const.py."""
    match = _EDITOR_VERSION.search(const)
    if match is None:
        raise SystemExit("EDITOR_VERSION not found in const.py")
    return match[2]


def newer_editor_version(latest_tag: str, const: str) -> str | None:
    """The version of the editor release ``latest_tag`` ("xknx-editor-v0.10.0") when the
    integration does not install it yet: newer than ``EDITOR_VERSION`` in ``const``."""
    if not latest_tag.startswith(EDITOR_TAG_PREFIX):
        raise SystemExit(f"not an editor release tag: {latest_tag}")
    version = latest_tag.removeprefix(EDITOR_TAG_PREFIX)
    if not _VERSION.match(version):
        raise SystemExit(f"not a release version: {version}")
    return version if version_key(version) > version_key(editor_version(const)) else None


def bumped_const(const: str, version: str) -> str:
    editor_version(const)
    return _EDITOR_VERSION.sub(rf"\g<1>{version}\g<3>", const, count=1)


def bumped_manifest(manifest: str, version: str) -> str:
    """``manifest`` with ``version``, its layout kept."""
    if "version" not in json.loads(manifest):
        raise SystemExit("no version in manifest.json")
    return _MANIFEST_VERSION.sub(rf"\g<1>{version}\g<3>", manifest, count=1)


def bumped_bug_report(bug_report: str, version: str) -> str:
    if _PLACEHOLDER.search(bug_report) is None:
        raise SystemExit("no Integration Version placeholder in bug_report.yml")
    return _PLACEHOLDER.sub(rf"\g<1>{version}\g<3>", bug_report, count=1)


def bump(version: str) -> None:
    """Install the editor ``version`` and give the integration that version."""
    for path, change in (
        (CONST, bumped_const),
        (MANIFEST, bumped_manifest),
        (BUG_REPORT, bumped_bug_report),
    ):
        text = path.read_text(encoding="utf-8")
        path.write_text(change(text, version), encoding="utf-8", newline="\n")


def release_notes(version: str, editor_notes: str) -> str:
    """The integration's release notes: which editor release it installs, then that release's
    notes, with their issue and pull request numbers linked to the editor's repository."""
    url = f"https://github.com/{EDITOR_REPO}/releases/tag/{EDITOR_TAG_PREFIX}{version}"
    notes = _ISSUE.sub(rf"{EDITOR_REPO}#\1", editor_notes.strip())
    lines = [f"Installs [XKNX Editor {version}]({url})."]
    if notes:
        lines += ["", "## XKNX Editor " + version, "", re.sub(r"^#", "##", notes, flags=re.M)]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="print the version of a newer editor release")
    check.add_argument("latest_tag", help="the editor's latest release tag")
    bump_ = commands.add_parser("bump", help="install this editor version")
    bump_.add_argument("version")
    notes = commands.add_parser("notes", help="print the release notes")
    notes.add_argument("version")
    notes.add_argument("editor_notes", type=Path, help="file with the editor release's notes")
    args = parser.parse_args()
    if args.command == "check":
        version = newer_editor_version(args.latest_tag, CONST.read_text(encoding="utf-8"))
        if version:
            print(version)
    elif args.command == "bump":
        if not _VERSION.match(args.version):
            raise SystemExit(f"not a release version: {args.version}")
        bump(args.version)
    else:
        sys.stdout.write(release_notes(args.version, args.editor_notes.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
