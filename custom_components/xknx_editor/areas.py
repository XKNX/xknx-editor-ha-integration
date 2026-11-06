"""Home Assistant's floors and areas for the editor's buildings.

The editor sends the floors and areas its project's buildings become, each with a key of the
project and the space. This module creates them, links them to a floor or area of the same name,
or renames the linked one, and remembers the links (key -> floor or area id) in Home Assistant's
storage, so a renamed space renames its floor or area. Nothing is ever removed.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_STORAGE_VERSION = 1
_STORAGE_KEY = f"{DOMAIN}.areas"


class AreaLinks:
    """The floors and areas the editor's spaces became."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, _STORAGE_VERSION, _STORAGE_KEY)
        self.links: dict[str, str] | None = None

    async def async_load(self) -> dict[str, str]:
        if self.links is None:
            stored = await self._store.async_load() or {}
            links = stored.get("links")
            self.links = (
                {str(k): str(v) for k, v in links.items()} if isinstance(links, dict) else {}
            )
        return self.links

    def snapshot(self) -> dict[str, Any]:
        """Home Assistant's floors and areas and the links, as the editor compares them."""
        floors = fr.async_get(self.hass).async_list_floors()
        areas = ar.async_get(self.hass).async_list_areas()
        return {
            "type": "areas",
            "floors": [{"floor_id": f.floor_id, "name": f.name, "level": f.level} for f in floors],
            "areas": [{"area_id": a.id, "name": a.name, "floor_id": a.floor_id} for a in areas],
            "links": dict(self.links or {}),
        }

    async def async_apply(self, floors: Any, areas: Any) -> int:
        """Create, link or rename the floors and areas the editor sent; returns how many it
        created, linked or renamed."""
        # Both checked before anything changes.
        floor_entries, area_entries = _entries(floors), _entries(areas)
        links = await self.async_load()
        floor_registry = fr.async_get(self.hass)
        area_registry = ar.async_get(self.hass)
        changed = 0
        for floor in floor_entries:
            key, name = str(floor["key"]), str(floor["name"])
            level = floor.get("level") if isinstance(floor.get("level"), int) else None
            linked = floor_registry.async_get_floor(links.get(key, ""))
            if linked is not None:
                if linked.name != name:
                    floor_registry.async_update(linked.floor_id, name=name)
                    changed += 1
                continue
            same = floor_registry.async_get_floor_by_name(name)
            if same is None:
                same = floor_registry.async_create(name, level=level)
            links[key] = same.floor_id
            changed += 1
        for area in area_entries:
            key, name = str(area["key"]), str(area["name"])
            floor_id = links.get(str(area.get("floor_key") or ""))
            if floor_id is not None and floor_registry.async_get_floor(floor_id) is None:
                floor_id = None
            linked_area = area_registry.async_get_area(links.get(key, ""))
            if linked_area is not None:
                if linked_area.name != name:
                    area_registry.async_update(linked_area.id, name=name)
                    changed += 1
                continue
            same_area = area_registry.async_get_area_by_name(name)
            if same_area is None:
                same_area = area_registry.async_create(name, floor_id=floor_id)
            elif same_area.floor_id is None and floor_id is not None:
                # An area of the same name on no floor: on the floor it is on here.
                area_registry.async_update(same_area.id, floor_id=floor_id)
            links[key] = same_area.id
            changed += 1
        await self._store.async_save({"links": links})
        return changed


def _entries(value: Any) -> list[dict[str, Any]]:
    """The floors or areas of a request: objects with a key and a name."""
    if not isinstance(value, list):
        raise ValueError("not a list of floors or areas")
    entries = [v for v in value if isinstance(v, dict) and v.get("key") and v.get("name")]
    if len(entries) != len(value):
        raise ValueError("a floor or area without a key or a name")
    return entries
