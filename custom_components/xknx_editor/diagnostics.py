"""Diagnostics: what an issue report about the editor needs (no password, no sessions)."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from homeassistant.core import HomeAssistant

from . import XknxEditorConfigEntry
from .const import CONF_REQUIREMENTS, EDITOR_VERSION
from .environment import python_tag


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: XknxEditorConfigEntry
) -> dict[str, Any]:
    manager = entry.runtime_data
    process = manager.process
    environment = manager.environment
    return {
        "editor_version": EDITOR_VERSION,
        "requirements": entry.options.get(CONF_REQUIREMENTS) or "release",
        "status": manager.status,
        "detail": manager.detail,
        "python": python_tag(),
        "platform": sys.platform,
        "environment": {
            "folder": str(environment.root),
            "exists": await asyncio.to_thread(environment.python.exists),
            "state": await asyncio.to_thread(environment.state),
        },
        # The .NET runtime and the key-extraction helpers (their state, the settings the editor
        # gets).
        "dotnet": {
            "state": await asyncio.to_thread(manager.dotnet.state),
            "environment": sorted(await asyncio.to_thread(manager.dotnet.environment)),
        },
        "process": {
            "running": process is not None and process.running,
            "runs": manager.run,
            "output": list(process.output) if process is not None else [],
        },
    }
