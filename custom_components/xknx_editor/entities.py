"""Entities of Home Assistant's KNX integration for the editor's building functions.

The editor sends each candidate with a key (its project and function), a platform, a name, the
KNX integration's configuration and the area its room became. This module checks candidates with
the KNX integration's own validation, creates the chosen ones as its panel does, puts them in
their area, and remembers which key became which entity (its "links"), so the editor shows them
as created.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .knx_bridge import KnxError, async_create_entity, validate_entity

_STORAGE_VERSION = 1
_STORAGE_KEY = f"{DOMAIN}.entities"


class EntityLinks:
    """The entities the editor's building functions became."""

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
        """The links of the entities that are still there."""
        registry = er.async_get(self.hass)
        return {
            "type": "entity_links",
            "links": {
                key: entity_id
                for key, entity_id in (self.links or {}).items()
                if registry.async_get(entity_id) is not None
            },
        }

    def check(self, entities: Any) -> dict[str, str | None]:
        """The KNX integration's verdict on each candidate: None (it takes it) or why not."""
        return {str(entity["key"]): _verdict(self.hass, entity) for entity in _candidates(entities)}

    async def async_create(self, entities: Any) -> tuple[dict[str, str], dict[str, str]]:
        """Create the candidates; returns the entity ids and the errors, by key."""
        links = await self.async_load()
        created: dict[str, str] = {}
        errors: dict[str, str] = {}
        registry = er.async_get(self.hass)
        areas = ar.async_get(self.hass)
        for entity in _candidates(entities):
            key = str(entity["key"])
            try:
                validated = validate_entity(
                    self.hass, str(entity["platform"]), str(entity["name"]), entity.get("knx")
                )
                entity_id = await async_create_entity(self.hass, validated)
            except KnxError as error:
                errors[key] = str(error)
                continue
            except Exception as error:  # one entity does not stop the others
                errors[key] = str(error) or type(error).__name__
                continue
            area_id = entity.get("area_id")
            if isinstance(area_id, str) and areas.async_get_area(area_id) is not None:
                registry.async_update_entity(entity_id, area_id=area_id)
            links[key] = entity_id
            created[key] = entity_id
        await self._store.async_save({"links": links})
        return created, errors


def _verdict(hass: HomeAssistant, entity: dict[str, Any]) -> str | None:
    try:
        validate_entity(hass, str(entity["platform"]), str(entity["name"]), entity.get("knx"))
    except KnxError as error:
        return str(error)
    return None


def _candidates(value: Any) -> list[dict[str, Any]]:
    """The candidates of a request: objects with a key, a platform and a name."""
    if not isinstance(value, list):
        raise ValueError("not a list of entities")
    candidates = [
        v
        for v in value
        if isinstance(v, dict) and v.get("key") and v.get("platform") and v.get("name")
    ]
    if len(candidates) != len(value):
        raise ValueError("an entity without a key, a platform or a name")
    return candidates
