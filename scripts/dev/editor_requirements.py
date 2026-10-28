"""Build what an editor release attaches for the integration from an editor checkout:

- wheels of the editor's own packages (and of xknxproject at the revision the editor pins, from a
  checkout of it) and the locked requirements file that names them under ``base_url``;
- ``keyextract.zip``: the editor's key-extraction helpers for the .NET runtime (built like in the
  editor's Docker image, with the .NET SDK), next to the requirements file, which names its hash.

To try an editor build in the test Home Assistant, set the integration's requirements option to
the file:

    uv run --python 3.14 scripts/dev/editor_requirements.py ../xknx-editor ../xknxproject \
        .dev/editor-release file:///mnt/c/.../.dev/editor-release

argv: editor checkout, xknxproject checkout, output folder, base URL of the wheels.
"""

import hashlib
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

repo, xknxproject, out, base_url = (
    Path(sys.argv[1]).resolve(),
    Path(sys.argv[2]).resolve(),
    Path(sys.argv[3]).resolve(),
    sys.argv[4].rstrip("/"),
)
out.mkdir(parents=True, exist_ok=True)
for old in out.glob("*.whl"):
    old.unlink()

# The helpers of the editor's Docker image: folder, source, extra package reference.
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


def run(*args: str, cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


def build_helpers(target: Path) -> None:
    """The key-extraction helpers as ``target`` (a zip with sign/, trace/, oauth/)."""
    sources = repo / "packages" / "proj" / "src" / "xknxeditor" / "proj" / "core"
    with tempfile.TemporaryDirectory() as temp:
        built = Path(temp) / "out"
        for name, source, package in HELPERS:
            project = Path(temp) / name
            project.mkdir()
            (project / source).write_bytes((sources / source).read_bytes())
            (project / "xknxkeyx.csproj").write_text(
                CSPROJ.format(source=source, package=package), encoding="utf-8"
            )
            run(
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


members = sorted(p.parent for p in repo.glob("packages/*/pyproject.toml")) + sorted(
    p.parent for p in repo.glob("apps/*/pyproject.toml")
)
for member in members:
    run("uv", "build", "--wheel", "--out-dir", str(out), str(member), cwd=repo)
run("uv", "build", "--wheel", "--out-dir", str(out), str(xknxproject), cwd=repo)
helpers = out / "keyextract.zip"
build_helpers(helpers)

exported = run(
    "uv",
    "export",
    "--frozen",
    "--no-dev",
    "--all-packages",
    "--no-emit-workspace",
    "--no-header",
    "--format",
    "requirements.txt",
    cwd=repo,
)
# The helpers next to this file, by hash (the integration checks it).
lines: list[str] = [f"# keyextract.zip sha256:{hashlib.sha256(helpers.read_bytes()).hexdigest()}"]
skip = False
for line in exported.splitlines():
    if line.startswith("xknxproject @ git+"):
        skip = True  # replaced by the wheel below, together with its hash lines
        continue
    if skip and line.startswith("    "):
        continue
    skip = False
    lines.append(line)

for wheel in sorted(out.glob("*.whl")):
    name = wheel.name.split("-")[0].replace("_", "-")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    lines.append(f"{name} @ {base_url}/{wheel.name} \\\n    --hash=sha256:{digest}")

(out / "requirements.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(len(list(out.glob("*.whl"))), "wheels,", len(lines), "requirement lines, keyextract.zip")
