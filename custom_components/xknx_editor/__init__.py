"""XKNX Editor: the KNX project editor in Home Assistant's sidebar.

The editor runs as a process of its own, in an environment of its own (see environment.py), and
its web UI reaches signed-in administrators (every signed-in user when the options allow it)
through the proxy in proxy.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_ALLOW_NON_ADMINS,
    CONF_EDITOR_PASSWORD,
    CONF_REQUIREMENTS,
    DOMAIN,
    EDITOR_VERSION,
    PANEL_ELEMENT,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL_PATH,
    REQUIREMENTS_URL,
    STATIC_URL,
)
from .manager import EditorManager
from .proxy import EditorProxyView, McpProxyView

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type XknxEditorConfigEntry = ConfigEntry[EditorManager]


def _manager(hass: HomeAssistant) -> EditorManager | None:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0].runtime_data if entries else None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register what lives as long as Home Assistant: the proxies of the UI and of MCP, the panel
    script and the websocket commands."""
    hass.http.register_view(EditorProxyView(lambda: _manager(hass)))
    hass.http.register_view(McpProxyView(lambda: _manager(hass)))
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), False)]
    )
    websocket_api.async_register_command(hass, websocket_session)
    websocket_api.async_register_command(hass, websocket_validate_session)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: XknxEditorConfigEntry) -> bool:
    """Start the editor and show it in the sidebar."""
    requirements = entry.options.get(CONF_REQUIREMENTS) or REQUIREMENTS_URL.format(
        version=EDITOR_VERSION
    )
    manager = EditorManager(
        hass,
        password=entry.data[CONF_EDITOR_PASSWORD],
        requirements=requirements,
        allow_non_admins=entry.options.get(CONF_ALLOW_NON_ADMINS, False),
    )
    entry.runtime_data = manager
    entry.async_create_background_task(hass, manager.async_run(), f"{DOMAIN} editor")
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_ELEMENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=f"{STATIC_URL}/panel.js?v={EDITOR_VERSION}",
        require_admin=not manager.allow_non_admins,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: XknxEditorConfigEntry) -> bool:
    """Stop the editor and leave the sidebar."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
    await entry.runtime_data.async_stop()
    return True


async def _async_options_updated(hass: HomeAssistant, entry: XknxEditorConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _allowed(
    connection: websocket_api.ActiveConnection, msg: dict[str, Any], manager: EditorManager | None
) -> bool:
    """Whether the user may use the panel: administrators, or everyone when the options allow it.
    Sends the error when not."""
    if manager is None:
        connection.send_error(msg["id"], "not_loaded", "XKNX Editor is not set up")
        return False
    if not (connection.user.is_admin or manager.allow_non_admins):
        connection.send_error(msg["id"], websocket_api.ERR_UNAUTHORIZED, "Administrators only")
        return False
    return True


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/session"})
@callback
def websocket_session(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """A new session for the panel."""
    manager = _manager(hass)
    if _allowed(connection, msg, manager):
        assert manager is not None
        connection.send_result(msg["id"], {"session": manager.sessions.create()})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/session/validate", vol.Required("session"): str}
)
@callback
def websocket_validate_session(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Keep the panel's session live; whether it still was."""
    manager = _manager(hass)
    if _allowed(connection, msg, manager):
        assert manager is not None
        connection.send_result(msg["id"], {"valid": manager.sessions.validate(msg["session"])})
