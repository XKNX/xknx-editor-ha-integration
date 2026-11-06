"""The requirements of Home Assistant integrations, as the installed Home Assistant pins them.

    python scripts/ha_requirements.py knx frontend > ha-requirements.txt

The tests against Home Assistant's beta (.github/workflows/tests-beta.yml) install whatever
Home Assistant release is newest, so the packages its KNX and frontend integrations need (pinned
for the stable release in requirements_test.txt) come from that release's own manifests.
Standard library only.
"""

from __future__ import annotations

import json
import sys
from importlib.util import find_spec
from pathlib import Path


def components_folder() -> Path:
    """The folder of the installed Home Assistant's integrations."""
    spec = find_spec("homeassistant")
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit("Home Assistant is not installed")
    return Path(next(iter(spec.submodule_search_locations))) / "components"


def requirements(components: list[str], folder: Path) -> list[str]:
    """The requirements of ``components`` (their domains), each once, in their order."""
    found: list[str] = []
    for domain in components:
        manifest = folder / domain / "manifest.json"
        if not manifest.is_file():
            raise SystemExit(f"Home Assistant has no integration {domain!r}")
        for requirement in json.loads(manifest.read_text(encoding="utf-8")).get("requirements", []):
            if requirement not in found:
                found.append(requirement)
    return found


def main(argv: list[str]) -> None:
    if not argv:
        raise SystemExit("usage: ha_requirements.py <domain> [<domain> ...]")
    print("\n".join(requirements(argv, components_folder())))


if __name__ == "__main__":
    main(sys.argv[1:])
