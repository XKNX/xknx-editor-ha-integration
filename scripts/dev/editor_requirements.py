"""Build what an editor release attaches for the integration from an editor checkout: wheels of
the editor's own packages (and of xknxproject at the revision the editor pins, from a checkout of
it) and the locked requirements file that names them under ``base_url``. To try an editor build
in the test Home Assistant, set the integration's requirements option to the file:

    uv run --python 3.14 scripts/dev/editor_requirements.py ../xknx-editor ../xknxproject \
        .dev/editor-release file:///mnt/c/.../.dev/editor-release

argv: editor checkout, xknxproject checkout, output folder, base URL of the wheels.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

repo, xknxproject, out, base_url = (
    Path(sys.argv[1]),
    Path(sys.argv[2]),
    Path(sys.argv[3]),
    sys.argv[4].rstrip("/"),
)
out.mkdir(parents=True, exist_ok=True)
for old in out.glob("*.whl"):
    old.unlink()


def run(*args: str, cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


members = sorted(p.parent for p in repo.glob("packages/*/pyproject.toml")) + sorted(
    p.parent for p in repo.glob("apps/*/pyproject.toml")
)
for member in members:
    run("uv", "build", "--wheel", "--out-dir", str(out), str(member), cwd=repo)
run("uv", "build", "--wheel", "--out-dir", str(out), str(xknxproject), cwd=repo)

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
lines: list[str] = []
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
print(len(list(out.glob("*.whl"))), "wheels,", len(lines), "requirement lines")
