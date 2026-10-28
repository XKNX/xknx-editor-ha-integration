"""Build the files a release of the integration installs the editor from.

    python scripts/release_files.py --editor ../xknx-editor --tag 0.2.0 --out release

(``--base-url`` in place of ``--tag`` to try an editor build before its release.)

The editor runs in a virtual environment of its own, filled from a locked requirements file of
the editor release the integration pins (``EDITOR_VERSION`` in const.py). The files are attached
to the integration's release ``--tag``, so the editor's releases keep only what its users download.
From an editor checkout at that release, it writes into ``--out``:

- ``xknx-editor-<version>-requirements.txt``: the third-party packages of the editor's
  ``uv.lock`` with their hashes, and the wheels below by their download URL and hash;
- the wheels of the editor's own packages, and of every package ``uv.lock`` takes from a git
  revision (xknxproject), built from a clone of that revision;
- ``xknx-editor-<version>-keyextract.zip``: the editor's key-extraction helpers for the .NET
  runtime, built like in its Docker image (needs the .NET SDK). The requirements file names its
  SHA-256 in a ``# keyextract.zip sha256:`` line, which the integration checks.

Needs uv, git and the .NET SDK on PATH. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

RELEASES_URL = "https://github.com/XKNX/xknx-editor-ha-integration/releases/download"
HELPERS_NAME = "keyextract.zip"
CONST = Path(__file__).resolve().parent.parent / "custom_components" / "xknx_editor" / "const.py"
_SOURCES = Path("packages") / "proj" / "src" / "xknxeditor" / "proj" / "core"

# The helpers of the editor's Docker image (docker/Dockerfile, dotnet-build): folder, source,
# extra package.
HELPERS = (
    ("sign", "_extract.cs", ""),
    (
        "trace",
        "_extract_trace.cs",
        '<PackageReference Include="System.Configuration.ConfigurationManager" '
        'Version="10.0.14" />',
    ),
    ("oauth", "_extract_oauth.cs", ""),
)
CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net10.0</TargetFramework>
    <Nullable>disable</Nullable>
    <ImplicitUsings>disable</ImplicitUsings>
    <AssemblyName>xknxkeyx</AssemblyName>
    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>
  </PropertyGroup>
  <ItemGroup>
    <Compile Include="{source}" />
    {package}
  </ItemGroup>
</Project>
"""
# "name @ git+https://host/owner/repo@revision" in the exported requirements
_GIT = re.compile(r"^(?P<name>[A-Za-z0-9._-]+) @ git\+(?P<url>\S+?)@(?P<rev>[0-9a-f]{7,40})$")
_EDITOR_VERSION = re.compile(r'^EDITOR_VERSION: Final = "([^"]+)"$', re.M)


def editor_version(const: str) -> str:
    """The editor release the integration installs, from the text of const.py."""
    match = _EDITOR_VERSION.search(const)
    if match is None:
        raise SystemExit("EDITOR_VERSION not found in const.py")
    return match[1]


def git_sources(exported: str) -> list[tuple[str, str, str]]:
    """The packages the exported requirements take from git: (name, URL, revision)."""
    return [
        (m["name"], m["url"], m["rev"]) for line in exported.splitlines() if (m := _GIT.match(line))
    ]


def requirements_text(exported: str, wheels: list[Path], base_url: str, helpers_sha256: str) -> str:
    """The requirements file: the exported packages without the ones from git (their wheels
    replace them), then every wheel by its URL under ``base_url`` and its hash."""
    lines = [f"# {HELPERS_NAME} sha256:{helpers_sha256}"]
    skip = False
    for line in exported.splitlines():
        if _GIT.match(line):
            skip = True  # its indented lines ("# via ...") go with it
            continue
        if skip and line.startswith("    "):
            continue
        skip = False
        lines.append(line)
    for wheel in sorted(wheels, key=lambda w: w.name):
        name = wheel.name.split("-")[0].replace("_", "-")
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        lines.append(f"{name} @ {base_url}/{wheel.name} \\\n    --hash=sha256:{digest}")
    return "\n".join(lines) + "\n"


def _run(*args: str, cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


def build_helpers(editor: Path, target: Path) -> None:
    """The key-extraction helpers as the zip ``target`` (folders sign/, trace/, oauth/)."""
    with tempfile.TemporaryDirectory() as temp:
        built = Path(temp) / "out"
        for name, source, package in HELPERS:
            project = Path(temp) / name
            project.mkdir()
            (project / source).write_bytes((editor / _SOURCES / source).read_bytes())
            (project / "xknxkeyx.csproj").write_text(
                CSPROJ.format(source=source, package=package), encoding="utf-8"
            )
            _run(
                "dotnet",
                "publish",
                "xknxkeyx.csproj",
                "-c",
                "Release",
                "-o",
                str(built / name),
                "-nologo",
                cwd=project,
            )
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(built.rglob("*")):
                if file.is_file() and file.suffix != ".pdb":
                    archive.write(file, file.relative_to(built).as_posix())


def build(editor: Path, base_url: str, out: Path) -> Path:
    """Write the release files into ``out``, the wheels named under ``base_url``; returns the
    requirements file."""
    version = editor_version(CONST.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    members = sorted(p.parent for p in editor.glob("packages/*/pyproject.toml"))
    members += sorted(p.parent for p in editor.glob("apps/*/pyproject.toml"))
    for member in members:
        _run("uv", "build", "--wheel", "--out-dir", str(out), str(member), cwd=editor)
    exported = _run(
        "uv",
        "export",
        "--frozen",
        "--no-dev",
        "--all-packages",
        "--no-emit-workspace",
        "--no-header",
        "--format",
        "requirements.txt",
        cwd=editor,
    )
    with tempfile.TemporaryDirectory() as temp:
        for name, url, rev in git_sources(exported):
            clone = Path(temp) / name
            _run("git", "clone", "--quiet", url, str(clone), cwd=editor)
            _run("git", "checkout", "--quiet", rev, cwd=clone)
            _run("uv", "build", "--wheel", "--out-dir", str(out), str(clone), cwd=editor)
    helpers = out / f"xknx-editor-{version}-{HELPERS_NAME}"
    build_helpers(editor, helpers)
    requirements = out / f"xknx-editor-{version}-requirements.txt"
    requirements.write_text(
        requirements_text(
            exported,
            list(out.glob("*.whl")),
            base_url.rstrip("/"),
            hashlib.sha256(helpers.read_bytes()).hexdigest(),
        ),
        encoding="utf-8",
    )
    return requirements


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the files a release of the integration installs the editor from."
    )
    parser.add_argument("--editor", required=True, type=Path, help="editor checkout at its release")
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--tag", help="the integration's release tag the files are attached to")
    where.add_argument(
        "--base-url",
        help="where the wheels are instead, to try an editor build before its release, e.g. "
        "file:///mnt/c/.../.dev/editor-release (pass the requirements file's path on the Home "
        "Assistant host as the integration's requirements option)",
    )
    parser.add_argument("--out", required=True, type=Path, help="folder for the files")
    args = parser.parse_args()
    base_url = args.base_url or f"{RELEASES_URL}/{args.tag}"
    requirements = build(args.editor.resolve(), base_url, args.out)
    print(f"wrote {requirements} and {len(list(args.out.glob('*.whl')))} wheels")


if __name__ == "__main__":
    main()
