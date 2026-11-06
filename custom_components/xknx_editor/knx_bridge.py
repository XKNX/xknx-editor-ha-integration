"""Home Assistant's KNX integration, as far as the editor uses it: the project it holds.

The KNX integration has no API for another integration to give it a project: the project is
uploaded in its panel, parsed with xknxproject and stored. The editor sends the same parsed form,
and this module stores and loads it the way the upload does (``KNXProject.process_project_file``
in ``homeassistant/components/knx/project.py``). Everything that reaches into the KNX integration
is in this module, so a change there is a change here only.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.core import callback as ha_callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# ``KNX_MODULE_KEY`` of the KNX integration (``HassKey("knx")``); its module is not imported, so
# this integration loads without the KNX integration's requirements.
_KNX_MODULE_KEY = "knx"
# ``SIGNAL_KNX_TELEGRAM`` of the KNX integration: (telegram, its dict) of each telegram.
_SIGNAL_TELEGRAM = "knx_telegram"
# The most telegrams a history gives.
_HISTORY_LIMIT = 50_000
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


def connection_info(hass: HomeAssistant) -> dict[str, Any] | None:
    """How the KNX integration reaches the bus: {type ("routing" or "tunneling"), host, port}."""
    knx = _knx(hass)
    entry = getattr(knx, "entry", None)
    data = getattr(entry, "data", None)
    if not isinstance(data, Mapping):
        return None
    kind = str(data.get("connection_type") or "")
    if kind.startswith("routing"):
        return {
            "type": "routing",
            "host": data.get("multicast_group"),
            "port": data.get("multicast_port"),
        }
    return {"type": "tunneling", "host": data.get("host"), "port": data.get("port")}


# The xknx devices the KNX integration sends with on its own, and what they are for the editor.
_SENDING_DEVICES = {
    "ExposeSensor": "expose",
    "TimeDevice": "time",
    "DateDevice": "time",
    "DateTimeDevice": "time",
}


def group_address_usage(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """What Home Assistant does with each group address, by its raw address (as text): its users
    (entities, exposes, the time server) and whether Home Assistant sends to it."""
    knx = _knx(hass)
    if knx is None or not hasattr(knx, "get_group_address_entities"):
        return {}
    from xknx.telegram.address import GroupAddress

    def raw_of(address: object) -> int | None:
        if isinstance(address, GroupAddress):
            return address.raw
        try:
            return GroupAddress(str(address)).raw
        except Exception:  # an internal address ("i-…") or none
            return None

    usage: dict[int, dict[str, Any]] = {}

    def entry(raw: int) -> dict[str, Any]:
        return usage.setdefault(raw, {"users": [], "sends": False})

    registry = er.async_get(hass)
    for address, entities in knx.get_group_address_entities().items():
        if (raw := raw_of(address)) is None:
            continue
        for entity in entities:
            entity_id = registry.async_get_entity_id(entity["platform"], "knx", entity["unique_id"])
            state = hass.states.get(entity_id) if entity_id is not None else None
            registered = registry.async_get(entity_id) if entity_id is not None else None
            name = (
                state.name
                if state is not None
                else (registered.name or registered.original_name)
                if registered is not None
                else None
            )
            entry(raw)["users"].append(
                {
                    "kind": "entity",
                    "entity_id": entity_id,
                    "name": name,
                    "platform": entity["platform"],
                    "state": state.state if state is not None else None,
                    "ui": entity["ui"],
                    "disabled": entity["disabled"],
                }
            )
    for device in list(knx.xknx.devices):
        kind = _SENDING_DEVICES.get(type(device).__name__)
        for remote_value in device._iter_remote_values():
            # Where the device writes: an entity's command, an expose, the time server.
            if (raw := raw_of(remote_value.group_address)) is None:
                continue
            entry(raw)["sends"] = True
            if kind is not None:
                entry(raw)["users"].append({"kind": kind, "name": device.name})
    return {str(raw): value for raw, value in sorted(usage.items())}


def latest_telegrams(hass: HomeAssistant) -> list[dict[str, Any]]:
    """The last telegram with a value of each group address the KNX integration saw."""
    knx = _knx(hass)
    telegrams = getattr(getattr(knx, "telegrams", None), "last_ga_telegrams", None)
    return [dict(t) for t in telegrams.values()] if isinstance(telegrams, dict) else []


def subscribe_telegrams(
    hass: HomeAssistant, callback: Callable[[dict[str, Any]], None]
) -> Callable[[], None]:
    """Call ``callback`` with each telegram the KNX integration sends or receives (its dict, as
    its group monitor shows it) until the returned function is called."""

    # In the event loop, where the caller's queue lives (not in an executor thread).
    @ha_callback
    def received(_telegram: object, telegram_dict: dict[str, Any]) -> None:
        callback(dict(telegram_dict))

    return async_dispatcher_connect(hass, _SIGNAL_TELEGRAM, received)


async def async_telegram_history(hass: HomeAssistant, hours: int) -> list[dict[str, Any]]:
    """The telegrams the KNX integration recorded in the last ``hours`` hours, oldest first."""
    knx = _knx(hass)
    telegrams = getattr(knx, "telegrams", None)
    store = getattr(telegrams, "store", None)
    if telegrams is None or store is None:
        raise KnxError("the KNX integration's telegram history is not ready")
    from knx_telegram_store import TelegramQuery

    query = TelegramQuery(
        start_time=dt_util.now() - timedelta(hours=hours),
        limit=_HISTORY_LIMIT,
        order_descending=False,
    )
    result = await store.query(query, flush_first=True)
    return [dict(telegrams.model_to_dict(t)) for t in result.telegrams]


def validate_entity(
    hass: HomeAssistant, platform: str, name: str, knx_config: Any
) -> dict[str, Any]:
    """The KNX integration's own check of an entity configuration, as its panel makes it;
    raises KnxError with why it does not take it."""
    if not knx_loaded(hass):
        raise KnxError("the KNX integration is not set up")
    try:
        from homeassistant.components.knx.storage.entity_store_validation import (
            EntityStoreValidationException,
            validate_entity_data,
        )
    except ImportError as error:
        raise KnxError(f"this version of the KNX integration is not supported: {error}") from error
    try:
        return validate_entity_data(
            {
                "platform": platform,
                "data": {
                    "entity": {"name": name, "device_info": None, "entity_category": None},
                    "knx": knx_config,
                },
            }
        )
    except EntityStoreValidationException as error:
        raise KnxError(_validation_text(error.validation_error)) from error


def _validation_text(validation_error: Any) -> str:
    """The KNX integration's validation errors as one text: each with where it is."""
    errors = validation_error.get("errors") if isinstance(validation_error, dict) else None
    if not isinstance(errors, list) or not errors:
        return str(validation_error)
    parts = []
    for error in errors:
        path = ".".join(str(p) for p in error.get("path") or [] if p != "data")
        message = str(error.get("error_message", ""))
        parts.append(f"{path}: {message}" if path else message)
    return "; ".join(parts)


async def async_create_entity(hass: HomeAssistant, validated: dict[str, Any]) -> str:
    """Create an entity the KNX integration validated (validate_entity), as its panel does;
    returns its entity id."""
    knx = _knx(hass)
    config_store = getattr(knx, "config_store", None)
    if config_store is None:
        raise KnxError("the KNX integration is not set up")
    entity_id = await config_store.create_entity(validated["platform"], validated["data"])
    if not entity_id:
        raise KnxError("the KNX integration created no entity")
    return str(entity_id)


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
