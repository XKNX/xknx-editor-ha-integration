"""The editor's buildings as Home Assistant's floors and areas: created, linked, renamed."""

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import floor_registry as fr

from custom_components.xknx_editor.areas import AreaLinks


def _floor(key: str, name: str, level: int | None = None) -> dict[str, Any]:
    return {"key": key, "name": name, "level": level}


def _area(key: str, name: str, floor_key: str | None = None) -> dict[str, Any]:
    return {"key": key, "name": name, "floor_key": floor_key}


async def test_floors_and_areas_are_created_and_linked(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    floors, areas = fr.async_get(hass), ar.async_get(hass)
    # One of the same name already: linked, put on its floor, not created again.
    existing = areas.async_create("Kitchen")
    links = AreaLinks(hass)
    changed = await links.async_apply(
        [_floor("g:2", "Ground Floor", 0)],
        [_area("g:3", "Kitchen", "g:2"), _area("g:4", "Hall", "g:2"), _area("g:5", "Stairs")],
    )
    assert changed == 4
    ground = floors.async_get_floor_by_name("Ground Floor")
    assert ground is not None and ground.level == 0
    kitchen = areas.async_get_area(existing.id)
    assert kitchen is not None and kitchen.floor_id == ground.floor_id
    hall = areas.async_get_area_by_name("Hall")
    assert hall is not None and hall.floor_id == ground.floor_id
    stairs = areas.async_get_area_by_name("Stairs")
    assert stairs is not None and stairs.floor_id is None
    assert links.links == {
        "g:2": ground.floor_id,
        "g:3": existing.id,
        "g:4": hall.id,
        "g:5": stairs.id,
    }
    # Kept for the next start.
    assert hass_storage["xknx_editor.areas"]["data"]["links"] == links.links
    snapshot = links.snapshot()
    assert snapshot["type"] == "areas"
    assert snapshot["location_name"] == hass.config.location_name
    assert {"floor_id": ground.floor_id, "name": "Ground Floor", "level": 0} in snapshot["floors"]
    assert {"area_id": hall.id, "name": "Hall", "floor_id": ground.floor_id} in snapshot["areas"]


async def test_spaces_made_from_floors_and_areas_link_to_them_as_they_are(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    floors, areas = fr.async_get(hass), ar.async_get(hass)
    ground = floors.async_create("EG", level=0)
    kitchen = areas.async_create("Küche", floor_id=ground.floor_id)
    links = AreaLinks(hass)
    # Named otherwise in the editor (a building's name in front): linked, not renamed.
    changed = await links.async_apply(
        [{**_floor("g:2", "Haus EG"), "floor_id": ground.floor_id}],
        [
            {**_area("g:3", "Küche", "g:2"), "area_id": kitchen.id},
            # One Home Assistant no longer has: made as usual.
            {**_area("g:4", "Bad", "g:2"), "area_id": "gone"},
        ],
    )
    assert changed == 3
    assert links.links["g:2"] == ground.floor_id and links.links["g:3"] == kitchen.id
    assert floors.async_get_floor(ground.floor_id).name == "EG"  # type: ignore[union-attr]
    bath = areas.async_get_area_by_name("Bad")
    assert bath is not None and links.links["g:4"] == bath.id
    assert bath.floor_id == ground.floor_id


async def test_a_renamed_space_renames_its_floor_or_area(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    links = AreaLinks(hass)
    await links.async_apply([_floor("g:2", "Ground Floor")], [_area("g:3", "Kitchen", "g:2")])
    # Linked as they are: nothing to do.
    assert await links.async_apply([_floor("g:2", "Ground Floor")], [_area("g:3", "Kitchen")]) == 0
    changed = await links.async_apply([_floor("g:2", "EG")], [_area("g:3", "Küche")])
    assert changed == 2
    assert fr.async_get(hass).async_get_floor_by_name("EG") is not None
    assert ar.async_get(hass).async_get_area_by_name("Küche") is not None
    assert ar.async_get(hass).async_get_area_by_name("Kitchen") is None

    # The links are read back after a restart.
    again = AreaLinks(hass)
    assert await again.async_load() == links.links


async def test_a_floor_or_area_removed_in_home_assistant_is_made_again(
    hass: HomeAssistant,
) -> None:
    links = AreaLinks(hass)
    await links.async_apply([_floor("g:2", "Ground Floor")], [_area("g:3", "Kitchen", "g:2")])
    assert links.links is not None
    fr.async_get(hass).async_delete(links.links["g:2"])
    ar.async_get(hass).async_delete(links.links["g:3"])
    assert (
        await links.async_apply([_floor("g:2", "Ground Floor")], [_area("g:3", "Kitchen", "g:2")])
        == 2
    )
    kitchen = ar.async_get(hass).async_get_area_by_name("Kitchen")
    ground = fr.async_get(hass).async_get_floor_by_name("Ground Floor")
    assert kitchen is not None and ground is not None
    assert kitchen.floor_id == ground.floor_id


@pytest.mark.parametrize(
    ("floors", "areas"),
    [
        ("text", []),
        ([], [{"key": "g:3"}]),
        ([{"name": "Floor"}], []),
    ],
)
async def test_a_broken_request_changes_nothing(
    hass: HomeAssistant, floors: Any, areas: Any
) -> None:
    links = AreaLinks(hass)
    with pytest.raises(ValueError):
        await links.async_apply(
            [_floor("g:2", "Floor"), *floors] if isinstance(floors, list) else floors, areas
        )
    assert list(fr.async_get(hass).async_list_floors()) == []
