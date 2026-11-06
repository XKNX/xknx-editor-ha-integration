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
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.core import callback as ha_callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.storage import STORAGE_DIR
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
    # The transport as configured: "tunneling", "tunneling_tcp", "tunneling_tcp_secure",
    # "routing", "routing_secure" or "automatic" (a gateway found each time).
    if kind.startswith("routing"):
        return {
            "type": "routing",
            "transport": kind,
            "host": data.get("multicast_group"),
            "port": data.get("multicast_port"),
        }
    return {
        "type": "tunneling",
        "transport": kind,
        "host": data.get("host"),
        "port": data.get("port"),
    }


async def async_send_group(hass: HomeAssistant, message: Any) -> None:
    """Send a group read or write for the editor over the KNX integration's connection, with
    its services knx.read and knx.send ({address, read} or {address, payload}: raw)."""
    if not knx_loaded(hass) or not isinstance(message, dict):
        raise KnxError("the KNX integration is not set up")
    address = str(message.get("address") or "")
    if message.get("read"):
        await hass.services.async_call("knx", "read", {"address": address}, blocking=True)
        return
    payload = message.get("payload")
    await hass.services.async_call(
        "knx", "send", {"address": address, "payload": payload}, blocking=True
    )


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


def entity_knx_config(hass: HomeAssistant, entity_id: str) -> dict[str, Any] | None:
    """The ``knx`` part of the stored configuration of an entity made in the KNX panel."""
    config_store = getattr(_knx(hass), "config_store", None)
    if config_store is None:
        return None
    try:
        config = config_store.get_entity_config(entity_id)
    except Exception:  # not one of the panel's (YAML), or gone
        return None
    knx_config = config.get("data", {}).get("knx") if isinstance(config, dict) else None
    return dict(knx_config) if isinstance(knx_config, dict) else None


async def async_update_entity_knx(hass: HomeAssistant, entity_id: str, knx_config: Any) -> None:
    """Give an entity of the KNX panel new group addresses (its name and the rest stay), checked
    by the KNX integration as its panel checks them."""
    config_store = getattr(_knx(hass), "config_store", None)
    if config_store is None:
        raise KnxError("the KNX integration is not set up")
    try:
        current = config_store.get_entity_config(entity_id)
    except Exception as error:
        raise KnxError(f"{entity_id} is not an entity of the KNX panel: {error}") from error
    if not isinstance(knx_config, dict):
        raise KnxError("not a KNX configuration")
    from homeassistant.components.knx.storage.entity_store_validation import (
        EntityStoreValidationException,
        validate_entity_data,
    )

    data = current["data"]
    # Its own entity part (name, device, category) stays; the group addresses are replaced.
    knx_new = {**(data.get("knx") or {}), **knx_config}
    try:
        validated = validate_entity_data(
            {
                "platform": current["platform"],
                "data": {"entity": data.get("entity") or {}, "knx": knx_new},
            }
        )
    except EntityStoreValidationException as error:
        raise KnxError(_validation_text(error.validation_error)) from error
    await config_store.update_entity(current["platform"], entity_id, validated["data"])


async def async_delete_entity(hass: HomeAssistant, entity_id: str) -> None:
    """Remove an entity made in the KNX panel."""
    config_store = getattr(_knx(hass), "config_store", None)
    if config_store is None:
        raise KnxError("the KNX integration is not set up")
    await config_store.delete_entity(entity_id)


def time_server(hass: HomeAssistant) -> dict[str, str | None] | None:
    """The group addresses the KNX integration's time server sends time, date and date with
    time to (None: none); None without the KNX integration."""
    config_store = getattr(_knx(hass), "config_store", None)
    if config_store is None:
        return None
    try:
        stored = config_store.get_time_server_config()
    except Exception:
        return None
    result: dict[str, str | None] = {}
    for kind in ("time", "date", "datetime"):
        entry = stored.get(kind) if isinstance(stored, dict) else None
        result[kind] = entry.get("write") if isinstance(entry, dict) else None
    return result


async def async_set_time_server(hass: HomeAssistant, addresses: Any) -> None:
    """Let the KNX integration's time server send to these group addresses ({time, date,
    datetime}: a printed address or None), as its panel does."""
    config_store = getattr(_knx(hass), "config_store", None)
    if config_store is None:
        raise KnxError("the KNX integration is not set up")
    if not isinstance(addresses, dict):
        raise KnxError("not a time server configuration")
    from homeassistant.components.knx.storage.entity_store_validation import (
        EntityStoreValidationException,
    )
    from homeassistant.components.knx.storage.time_server import validate_time_server_data

    data = {
        kind: {"write": address}
        for kind in ("time", "date", "datetime")
        if isinstance(address := addresses.get(kind), str) and address
    }
    try:
        config = validate_time_server_data(data)
    except EntityStoreValidationException as error:
        raise KnxError(_validation_text(error.validation_error)) from error
    await config_store.update_time_server_config(config)


# Where the KNX integration keeps an uploaded keyring, below the storage folder.
_KEYRING_FILE = "knx/keyring.knxkeys"


def keyring_allowed(hass: HomeAssistant) -> str | None:
    """Why the editor's keyring cannot be the KNX integration's (None: it can). A secure
    connection takes its own credentials from the keyring: that one stays the user's."""
    knx = _knx(hass)
    entry = getattr(knx, "entry", None)
    if entry is None:
        return "the KNX integration is not set up"
    if str(entry.data.get("connection_type", "")).endswith("secure"):
        return "the KNX integration connects with KNX Secure: change its keyring in its options"
    return None


async def async_set_keyring(hass: HomeAssistant, keyring: bytes, password: str) -> None:
    """Make the editor's keyring (its Data Secure group keys) the KNX integration's, as an
    upload in its options does, and load the integration again."""
    if (reason := keyring_allowed(hass)) is not None:
        raise KnxError(reason)
    knx = _knx(hass)
    entry = knx.entry
    from xknx.exceptions.exception import InvalidSecureConfiguration
    from xknx.secure.keyring import sync_load_keyring

    path = Path(hass.config.path(STORAGE_DIR, _KEYRING_FILE))

    def write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".partial")
        partial.write_bytes(keyring)
        try:
            sync_load_keyring(path=partial, password=password)
        except InvalidSecureConfiguration:
            partial.unlink(missing_ok=True)
            raise
        partial.replace(path)

    try:
        await hass.async_add_executor_job(write)
    except InvalidSecureConfiguration as error:
        raise KnxError(f"the keyring is not valid: {error}") from error
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, "knxkeys_filename": _KEYRING_FILE, "knxkeys_password": password},
    )
    hass.config_entries.async_schedule_reload(entry.entry_id)


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
