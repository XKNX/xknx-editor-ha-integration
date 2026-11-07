"""Entities for the editor's building functions: checked and created as the KNX panel does,
following their functions; the KNX integration's time server and keys."""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.xknx_editor.entities import EntityLinks
from custom_components.xknx_editor.knx_bridge import (
    KnxError,
    async_set_keyring,
    async_set_time_server,
    keyring_allowed,
    time_server,
)

# The KNX integration's own validation and storage form.
pytest.importorskip("homeassistant.components.knx.storage.entity_store_validation")
to_storage_dict = pytest.importorskip(
    "homeassistant.components.knx.storage.config_store"
).to_storage_dict


class _ConfigStore:
    """The KNX integration's store of UI entities and of its time server, as stored."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.created: list[tuple[str, Any]] = []
        self.stored: dict[str, dict[str, Any]] = {}
        self.time_server: dict[str, Any] = {}

    async def create_entity(self, platform: str, data: Any) -> str:
        self.created.append((platform, data))
        entry = er.async_get(self.hass).async_get_or_create(
            platform, "knx", f"knx_es_{len(self.created)}", suggested_object_id=data.entity.name
        )
        self.stored[entry.entity_id] = {"platform": platform, "data": to_storage_dict(data)}
        return entry.entity_id

    def get_entity_config(self, entity_id: str) -> dict[str, Any]:
        return self.stored[entity_id]

    async def update_entity(self, platform: str, entity_id: str, data: Any) -> None:
        self.stored[entity_id] = {"platform": platform, "data": to_storage_dict(data)}

    async def delete_entity(self, entity_id: str) -> None:
        del self.stored[entity_id]
        er.async_get(self.hass).async_remove(entity_id)

    def get_time_server_config(self) -> dict[str, Any]:
        return self.time_server

    async def update_time_server_config(self, config: Any) -> None:
        self.time_server = {
            kind: {"write": getattr(config, kind).write}
            for kind in ("time", "date", "datetime")
            if getattr(config, kind) is not None
        }


class _KnxModule:
    def __init__(self, hass: HomeAssistant) -> None:
        self.project = object()
        self.config_store = _ConfigStore(hass)
        self.entry = MockConfigEntry(domain="knx", data={"connection_type": "tunneling"})


def _light(key: str, name: str, area_id: str | None = None, **knx: Any) -> dict[str, Any]:
    return {
        "key": key,
        "platform": "light",
        "name": name,
        "knx": knx or {"ga_switch": {"write": "1/1/1", "state": None, "passive": []}},
        "area_id": area_id,
    }


async def test_candidates_are_checked_by_the_knx_integration(hass: HomeAssistant) -> None:
    hass.data["knx"] = _KnxModule(hass)
    links = EntityLinks(hass)
    checks = links.check(
        [
            _light("g:function:1", "Ceiling"),
            # A light needs its switching address.
            _light("g:function:2", "Broken", ga_switch={"write": None, "state": None}),
        ]
    )
    assert checks["g:function:1"] is None
    assert checks["g:function:2"] and "ga_switch" in checks["g:function:2"]
    with pytest.raises(ValueError):
        links.check([{"key": "x"}])


async def test_without_the_knx_integration_nothing_is_taken(hass: HomeAssistant) -> None:
    checks = EntityLinks(hass).check([_light("g:function:1", "Ceiling")])
    assert checks == {"g:function:1": "the KNX integration is not set up"}


async def test_chosen_ones_are_created_in_their_area(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    knx = _KnxModule(hass)
    hass.data["knx"] = knx
    kitchen = ar.async_get(hass).async_create("Kitchen")
    links = EntityLinks(hass)
    created, errors = await links.async_create(
        [
            _light("g:function:1", "Ceiling", kitchen.id),
            _light("g:function:2", "Broken", ga_switch={"write": None, "state": None}),
            # An area Home Assistant does not have: none.
            _light("g:function:3", "Wall", "gone"),
        ]
    )
    assert set(created) == {"g:function:1", "g:function:3"}
    assert set(errors) == {"g:function:2"}
    registry = er.async_get(hass)
    ceiling = registry.async_get(created["g:function:1"])
    assert ceiling is not None and ceiling.area_id == kitchen.id
    wall = registry.async_get(created["g:function:3"])
    assert wall is not None and wall.area_id is None
    assert [platform for platform, _data in knx.config_store.created] == ["light", "light"]
    # Kept for the next start; an entity removed since is left out of what the editor gets.
    assert hass_storage["xknx_editor.entities"]["data"]["links"] == created
    registry.async_remove(created["g:function:3"])
    ceiling_id = created["g:function:1"]
    snapshot = links.snapshot()
    assert snapshot["links"] == {"g:function:1": ceiling_id}
    assert snapshot["configs"][ceiling_id]["ga_switch"]["write"] == "1/1/1"
    again = EntityLinks(hass)
    assert await again.async_load() == created


async def test_an_entity_follows_its_function(hass: HomeAssistant) -> None:
    knx = _KnxModule(hass)
    hass.data["knx"] = knx
    links = EntityLinks(hass)
    created, _errors = await links.async_create([_light("g:function:1", "Ceiling")])
    entity_id = created["g:function:1"]

    updated, errors = await links.async_update(
        [
            {
                "key": "g:function:1",
                "knx": {"ga_switch": {"write": "2/1/1", "state": "2/1/2", "passive": []}},
            },
            # Not one of the editor's, and a configuration the KNX integration does not take.
            {"key": "g:function:9", "knx": {}},
        ]
    )
    assert updated == ["g:function:1"]
    assert set(errors) == {"g:function:9"}
    stored = knx.config_store.stored[entity_id]["data"]
    assert stored["knx"]["ga_switch"]["write"] == "2/1/1"
    # Its name stays.
    assert stored["entity"]["name"] == "Ceiling"
    updated, errors = await links.async_update(
        [{"key": "g:function:1", "knx": {"ga_switch": {"write": None, "state": None}}}]
    )
    assert updated == [] and "ga_switch" in errors["g:function:1"]

    # The function is gone: so is its entity.
    removed, errors = await links.async_delete(["g:function:1", "g:function:9"])
    assert removed == ["g:function:1"]
    assert set(errors) == {"g:function:9"}
    assert links.links == {}
    assert er.async_get(hass).async_get(entity_id) is None
    with pytest.raises(ValueError):
        await links.async_delete("g:function:1")


async def test_a_climate_entity_keeps_its_settings_from_home_assistant(
    hass: HomeAssistant,
) -> None:
    knx = _KnxModule(hass)
    hass.data["knx"] = knx
    links = EntityLinks(hass)

    def heating(setpoint: str) -> dict[str, Any]:
        return {
            "target_temperature": {
                "ga_temperature_target": {"write": setpoint, "state": setpoint, "passive": []}
            },
            "ga_temperature_current": {"write": None, "state": "3/1/1", "passive": []},
            "ga_valve": {"write": None, "state": "3/1/3", "passive": []},
        }

    candidate = {
        "key": "g:function:5",
        "platform": "climate",
        "name": "Radiator",
        "knx": heating("3/1/2"),
        "area_id": None,
    }
    assert links.check([candidate]) == {"g:function:5": None}
    created, errors = await links.async_create([candidate])
    assert errors == {}
    entity_id = created["g:function:5"]
    stored = knx.config_store.stored[entity_id]["data"]["knx"]
    # The KNX integration's defaults for what the function does not say.
    assert stored["target_temperature"]["min_temp"] == 7
    # Changed in Home Assistant: other limits, a passive address of the room temperature.
    stored["target_temperature"]["min_temp"] = 15
    stored["ga_temperature_current"]["passive"] = ["3/1/9"]

    updated, errors = await links.async_update([{"key": "g:function:5", "knx": heating("3/2/2")}])
    assert (updated, errors) == (["g:function:5"], {})
    stored = knx.config_store.stored[entity_id]["data"]["knx"]
    target = stored["target_temperature"]
    assert target["ga_temperature_target"]["write"] == "3/2/2"
    assert target["ga_temperature_target"]["state"] == "3/2/2"
    assert target["min_temp"] == 15
    assert stored["ga_temperature_current"]["passive"] == ["3/1/9"]


async def test_the_time_server(hass: HomeAssistant) -> None:
    assert time_server(hass) is None
    knx = _KnxModule(hass)
    hass.data["knx"] = knx
    assert time_server(hass) == {"time": None, "date": None, "datetime": None}
    await async_set_time_server(hass, {"time": "0/0/1", "date": "0/0/2", "datetime": None})
    assert time_server(hass) == {"time": "0/0/1", "date": "0/0/2", "datetime": None}
    with pytest.raises(KnxError):
        await async_set_time_server(hass, {"time": "not an address"})
    with pytest.raises(KnxError):
        await async_set_time_server(hass, "x")


async def test_the_editors_keys(hass: HomeAssistant, tmp_path: Any) -> None:
    hass.config.config_dir = str(tmp_path)
    keyring = (Path(__file__).parent / "fixtures" / "testcase.knxkeys").read_bytes()
    assert keyring_allowed(hass) == "the KNX integration is not set up"
    knx = _KnxModule(hass)
    knx.entry.add_to_hass(hass)
    hass.data["knx"] = knx
    assert keyring_allowed(hass) is None

    with pytest.raises(KnxError, match="not valid"):
        await async_set_keyring(hass, keyring, "wrong")
    assert not (tmp_path / ".storage" / "knx" / "keyring.knxkeys").exists()

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await async_set_keyring(hass, keyring, "password")
    assert (tmp_path / ".storage" / "knx" / "keyring.knxkeys").read_bytes() == keyring
    assert knx.entry.data["knxkeys_filename"] == "knx/keyring.knxkeys"
    assert knx.entry.data["knxkeys_password"] == "password"
    reload.assert_called_once_with(knx.entry.entry_id)

    # A secure connection takes its credentials from its keyring: it stays.
    hass.config_entries.async_update_entry(
        knx.entry, data={**knx.entry.data, "connection_type": "tunneling_tcp_secure"}
    )
    assert keyring_allowed(hass) is not None
    with pytest.raises(KnxError, match="KNX Secure"):
        await async_set_keyring(hass, keyring, "password")
