"""Entities for the editor's building functions: checked and created as the KNX panel does."""

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er

from custom_components.xknx_editor.entities import EntityLinks

# The KNX integration's own validation.
pytest.importorskip("homeassistant.components.knx.storage.entity_store_validation")


class _ConfigStore:
    """The KNX integration's store of UI entities: it registers what it creates."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.created: list[tuple[str, Any]] = []

    async def create_entity(self, platform: str, data: Any) -> str:
        self.created.append((platform, data))
        entry = er.async_get(self.hass).async_get_or_create(
            platform, "knx", f"knx_es_{len(self.created)}", suggested_object_id=data.entity.name
        )
        return entry.entity_id


class _KnxModule:
    def __init__(self, hass: HomeAssistant) -> None:
        self.project = object()
        self.config_store = _ConfigStore(hass)


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
    assert links.snapshot() == {
        "type": "entity_links",
        "links": {"g:function:1": created["g:function:1"]},
    }
    again = EntityLinks(hass)
    assert await again.async_load() == created
