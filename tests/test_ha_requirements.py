"""The requirements of Home Assistant's integrations for the tests against its beta."""

import json
from pathlib import Path

import pytest

from scripts import ha_requirements


def _manifest(folder: Path, domain: str, requirements: list[str]) -> None:
    (folder / domain).mkdir(parents=True)
    (folder / domain / "manifest.json").write_text(
        json.dumps({"domain": domain, "requirements": requirements})
    )


def test_requirements_of_the_installed_home_assistant() -> None:
    found = ha_requirements.requirements(["knx"], ha_requirements.components_folder())
    # The KNX integration's own packages, pinned as Home Assistant pins them.
    assert any(r.startswith("xknx==") for r in found)
    assert any(r.startswith("xknxproject==") for r in found)


def test_each_requirement_once(tmp_path: Path) -> None:
    _manifest(tmp_path, "knx", ["xknx==4.1.0", "shared==1"])
    _manifest(tmp_path, "frontend", ["shared==1", "home-assistant-frontend==20261028.4"])
    _manifest(tmp_path, "zone", [])
    assert ha_requirements.requirements(["knx", "frontend", "zone"], tmp_path) == [
        "xknx==4.1.0",
        "shared==1",
        "home-assistant-frontend==20261028.4",
    ]
    with pytest.raises(SystemExit, match="no integration 'gone'"):
        ha_requirements.requirements(["gone"], tmp_path)


def test_command_line(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    folder = Path(__file__).parent
    monkeypatch.setattr(ha_requirements, "components_folder", lambda: folder)
    monkeypatch.setattr(ha_requirements, "requirements", lambda domains, _folder: domains)
    ha_requirements.main(["knx", "frontend"])
    assert capsys.readouterr().out == "knx\nfrontend\n"
    with pytest.raises(SystemExit, match="usage"):
        ha_requirements.main([])
