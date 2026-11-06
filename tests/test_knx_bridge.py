"""The editor's project in Home Assistant's KNX integration: stored and loaded as an upload is."""

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry

from custom_components.xknx_editor.knx_bridge import (
    KnxError,
    async_set_project,
    knx_loaded,
    project_info,
)

# The KNX integration's own classes: what the bridge reaches into.
knx_project = pytest.importorskip("homeassistant.components.knx.project")
xknx_module = pytest.importorskip("xknx")


def _ga(address: str) -> Any:
    return xknx_module.telegram.address.GroupAddress(address)


class _KnxModule:
    """The part of the KNX integration's module the project uses."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.xknx = xknx_module.XKNX()
        self.project = knx_project.KNXProject(hass, MockConfigEntry(domain="knx"))


def _project(*addresses: str, name: str = "House") -> dict[str, Any]:
    return {
        "info": {
            "project_id": "P-0001",
            "name": name,
            "last_modified": "2026-11-06T10:00:00",
            "group_address_style": "ThreeLevel",
            "guid": "guid-1",
            "created_by": "XKNX Editor",
            "schema_version": "23",
            "tool_version": "XKNX Editor 0.9.12",
            "xknxproject_version": "3.10.0",
            "language_code": "de-DE",
        },
        "communication_objects": {},
        "devices": {},
        "topology": {},
        "locations": {},
        "trades": [],
        "group_addresses": {
            address: {
                "name": f"Light {address}",
                "identifier": f"GA-{index}",
                "raw_address": index,
                "address": address,
                "project_uid": None,
                "dpt": {"main": 1, "sub": 1},
                "data_secure": False,
                "communication_object_ids": [],
                "description": "",
                "comment": "",
            }
            for index, address in enumerate(addresses, start=1)
        },
        "group_ranges": {},
        "functions": {},
    }


@pytest.fixture
def knx(hass: HomeAssistant) -> _KnxModule:
    module = _KnxModule(hass)
    hass.data["knx"] = module
    return module


async def test_the_project_is_stored_and_loaded(
    hass: HomeAssistant, knx: _KnxModule, hass_storage: dict[str, Any]
) -> None:
    assert knx_loaded(hass)
    assert project_info(hass) is None

    await async_set_project(hass, _project("1/2/3", "1/2/4"))

    assert project_info(hass) == {
        "name": "House",
        "guid": "guid-1",
        "tool_version": "XKNX Editor 0.9.12",
        "created_by": "XKNX Editor",
        "last_modified": "2026-11-06T10:00:00",
    }
    assert knx.project.loaded
    assert set(knx.project.group_addresses) == {"1/2/3", "1/2/4"}
    assert knx.project.group_addresses["1/2/3"].name == "Light 1/2/3"
    # Telegrams of the group address are decoded with its DPT.
    assert knx.xknx.group_address_dpt.get(_ga("1/2/3")) is not None
    # Stored as an upload is, for the next start.
    assert hass_storage["knx/knx_project.json"]["data"]["info"]["guid"] == "guid-1"
    # The project view and the entity suggestions read the new project.
    assert (await knx.project.get_knxproject())["info"]["name"] == "House"


async def test_a_new_project_leaves_nothing_of_the_old_one(
    hass: HomeAssistant, knx: _KnxModule
) -> None:
    await async_set_project(hass, _project("1/2/3", "1/2/4"))
    await knx.project.get_knxproject()
    await async_set_project(hass, _project("1/2/4", name="House 2"))

    assert set(knx.project.group_addresses) == {"1/2/4"}
    assert knx.xknx.group_address_dpt.get(_ga("1/2/3")) is None
    assert (await knx.project.get_knxproject())["info"]["name"] == "House 2"


@pytest.mark.parametrize(
    ("project", "error"),
    [
        ("text", "not an object"),
        ({"info": {}}, "no communication_objects"),
        ({**_project(), "devices": []}, "no devices"),
        ({**_project(), "trades": {}}, "trades are not a list"),
    ],
)
async def test_a_broken_project_is_refused(
    hass: HomeAssistant, knx: _KnxModule, project: Any, error: str
) -> None:
    await async_set_project(hass, _project("1/2/3"))
    with pytest.raises(KnxError, match=error):
        await async_set_project(hass, project)
    # The project held before stays.
    assert set(knx.project.group_addresses) == {"1/2/3"}


async def test_without_the_knx_integration(hass: HomeAssistant) -> None:
    assert not knx_loaded(hass)
    assert project_info(hass) is None
    with pytest.raises(KnxError, match="not set up"):
        await async_set_project(hass, _project("1/2/3"))


async def test_a_knx_integration_without_the_parts_used(hass: HomeAssistant) -> None:
    class _Other:
        def __init__(self) -> None:
            self.project = object()

    hass.data["knx"] = _Other()
    with pytest.raises(KnxError, match="not supported"):
        await async_set_project(hass, _project("1/2/3"))
