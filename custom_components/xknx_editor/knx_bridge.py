"""Home Assistant's KNX integration, as far as the editor uses it: the project it holds.

The KNX integration has no API for another integration to give it a project: the project is
uploaded in its panel, parsed with xknxproject and stored. The editor sends the same parsed form,
and this module stores and loads it the way the upload does (``KNXProject.process_project_file``
in ``homeassistant/components/knx/project.py``). Everything that reaches into the KNX integration
is in this module, so a change there is a change here only.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# ``KNX_MODULE_KEY`` of the KNX integration (``HassKey("knx")``); its module is not imported, so
# this integration loads without the KNX integration's requirements.
_KNX_MODULE_KEY = "knx"
# What the editor's project has to have to be taken (xknxproject's ``KNXProject``).
_SECTIONS = {
    "info": dict,
    "communication_objects": dict,
    "devices": dict,
    "topology": dict,
    "locations": dict,
    "group_addresses": dict,
    "group_ranges": dict,
    "functions": dict,
}
_INFO = ("name", "guid", "tool_version", "created_by", "last_modified")


class KnxError(Exception):
    """The KNX integration cannot take the project."""


def _knx(hass: HomeAssistant) -> Any | None:
    return hass.data.get(_KNX_MODULE_KEY)


def knx_loaded(hass: HomeAssistant) -> bool:
    """Whether the KNX integration is set up and loaded."""
    knx = _knx(hass)
    return knx is not None and hasattr(knx, "project")


def project_info(hass: HomeAssistant) -> dict[str, Any] | None:
    """Name, GUID, tool, creator and change date of the project the KNX integration holds."""
    knx = _knx(hass)
    info = getattr(getattr(knx, "project", None), "info", None) if knx is not None else None
    if not isinstance(info, dict):
        return None
    return {key: info.get(key) for key in _INFO}


def _check(project: Any) -> dict[str, Any]:
    if not isinstance(project, dict):
        raise KnxError("the project is not an object")
    for section, kind in _SECTIONS.items():
        if not isinstance(project.get(section), kind):
            raise KnxError(f"the project has no {section}")
    if not isinstance(project.get("trades", []), list):
        raise KnxError("the project's trades are not a list")
    return project


async def async_set_project(hass: HomeAssistant, project: Any) -> None:
    """Make ``project`` the KNX integration's project, stored as an uploaded one is."""
    data = _check(project)
    knx = _knx(hass)
    if knx is None or not hasattr(knx, "project"):
        raise KnxError("the KNX integration is not set up")
    holder = knx.project
    if (set_project := getattr(holder, "async_set_project", None)) is not None:
        # A KNX integration that takes project data from other sources itself.
        await set_project(knx.xknx, data)
    else:
        try:
            lock = holder._project_lock
            store = holder._store
        except AttributeError as error:
            raise KnxError(
                f"this version of the KNX integration is not supported: {error}"
            ) from error
        async with lock:
            await store.async_save(data)
            # Loading adds to what is loaded: start from nothing, so removed addresses go.
            holder.initial_state()
            await holder.load_project(knx.xknx, data=data)
    _LOGGER.debug(
        "KNX project from the editor: %s group addresses, %s devices",
        len(data["group_addresses"]),
        len(data["devices"]),
    )
