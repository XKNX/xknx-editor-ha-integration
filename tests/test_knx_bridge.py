"""The editor's project in Home Assistant's KNX integration: stored and loaded as an upload is."""

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import MockConfigEntry

from custom_components.xknx_editor.knx_bridge import (
    KnxError,
    async_set_project,
    async_telegram_history,
    connection_info,
    group_address_usage,
    knx_loaded,
    latest_telegrams,
    project_info,
    subscribe_telegrams,
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
        # The entities by group address (KNXModule.get_group_address_entities).
        self.entities: dict[str, list[dict[str, Any]]] = {}

    def get_group_address_entities(self) -> dict[str, list[dict[str, Any]]]:
        return self.entities


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


async def test_a_knx_integration_with_its_own_way_is_used(hass: HomeAssistant) -> None:
    taken: list[Any] = []

    class _Project:
        async def async_set_project(self, xknx: Any, project: Any) -> None:
            taken.append((xknx, project))

    class _Module:
        def __init__(self) -> None:
            self.xknx = object()
            self.project = _Project()

    module = _Module()
    hass.data["knx"] = module
    project = _project("1/2/3")
    await async_set_project(hass, project)
    assert taken == [(module.xknx, project)]


async def test_what_home_assistant_does_with_the_group_addresses(
    hass: HomeAssistant, knx: _KnxModule
) -> None:
    devices = xknx_module.devices
    for device in (
        devices.Switch(
            knx.xknx, name="Kitchen", group_address="1/2/3", group_address_state="1/2/4"
        ),
        devices.BinarySensor(knx.xknx, name="Window", group_address_state="1/2/5"),
        devices.ExposeSensor(
            knx.xknx, name="sensor.sun state", group_address="1/2/9", value_type="percent"
        ),
        devices.TimeDevice(knx.xknx, name="Time", group_address="0/0/1"),
    ):
        knx.xknx.devices.async_add(device)
    registry = er.async_get(hass)
    registry.async_get_or_create("switch", "knx", "u1", suggested_object_id="kitchen")
    registry.async_get_or_create(
        "binary_sensor", "knx", "u2", suggested_object_id="window", original_name="Window"
    )
    hass.states.async_set("switch.kitchen", "on", {"friendly_name": "Kitchen Light"})
    knx.entities = {
        "1/2/3": [{"platform": "switch", "unique_id": "u1", "ui": True, "disabled": False}],
        "1/2/4": [{"platform": "switch", "unique_id": "u1", "ui": True, "disabled": False}],
        "1/2/5": [{"platform": "binary_sensor", "unique_id": "u2", "ui": False, "disabled": True}],
        # An internal address is not a group address of the project.
        "i-test": [{"platform": "switch", "unique_id": "u1", "ui": True, "disabled": False}],
    }
    usage = group_address_usage(hass)
    kitchen = {
        "kind": "entity",
        "entity_id": "switch.kitchen",
        "name": "Kitchen Light",
        "platform": "switch",
        "state": "on",
        "ui": True,
        "disabled": False,
    }
    assert usage == {
        str(_ga("0/0/1").raw): {"users": [{"kind": "time", "name": "Time"}], "sends": True},
        str(_ga("1/2/3").raw): {"users": [kitchen], "sends": True},
        str(_ga("1/2/4").raw): {"users": [kitchen], "sends": False},
        str(_ga("1/2/5").raw): {
            "users": [
                {
                    "kind": "entity",
                    "entity_id": "binary_sensor.window",
                    "name": "Window",
                    "platform": "binary_sensor",
                    "state": None,
                    "ui": False,
                    "disabled": True,
                }
            ],
            "sends": False,
        },
        str(_ga("1/2/9").raw): {
            "users": [{"kind": "expose", "name": "sensor.sun state"}],
            "sends": True,
        },
    }


async def test_the_knx_integrations_connection(hass: HomeAssistant, knx: _KnxModule) -> None:
    assert connection_info(hass) is None
    knx.entry = MockConfigEntry(
        domain="knx",
        data={
            "connection_type": "routing",
            "multicast_group": "224.0.23.12",
            "multicast_port": 3671,
        },
    )
    assert connection_info(hass) == {"type": "routing", "host": "224.0.23.12", "port": 3671}
    knx.entry = MockConfigEntry(
        domain="knx",
        data={"connection_type": "tunneling_tcp", "host": "192.168.1.10", "port": 3671},
    )
    assert connection_info(hass) == {"type": "tunneling", "host": "192.168.1.10", "port": 3671}


async def test_telegrams_of_the_knx_integration(hass: HomeAssistant, knx: _KnxModule) -> None:
    telegram = {"destination": "1/2/3", "payload": 1}
    knx.telegrams = SimpleNamespace(last_ga_telegrams={"1/2/3": telegram}, store=None)
    assert latest_telegrams(hass) == [telegram]

    received: list[dict[str, Any]] = []
    unsubscribe = subscribe_telegrams(hass, received.append)
    async_dispatcher_send(hass, "knx_telegram", object(), telegram)
    unsubscribe()
    async_dispatcher_send(hass, "knx_telegram", object(), telegram)
    assert received == [telegram]

    with pytest.raises(KnxError, match="not ready"):
        await async_telegram_history(hass, 24)

    queries: list[Any] = []

    class _Store:
        async def query(self, query: Any, flush_first: bool) -> Any:
            queries.append((query, flush_first))
            return SimpleNamespace(telegrams=["m1", "m2"])

    knx.telegrams = SimpleNamespace(
        last_ga_telegrams={}, store=_Store(), model_to_dict=lambda m: {"model": m}
    )
    assert await async_telegram_history(hass, 6) == [{"model": "m1"}, {"model": "m2"}]
    query, flush_first = queries[0]
    assert flush_first
    assert query.order_descending is False
    assert dt_util.now() - query.start_time > timedelta(hours=5, minutes=59)
