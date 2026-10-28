"""Keeps the editor running: installs its environment, starts it, and starts it again when it
ends on its own."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DATA_FOLDER, EDITOR_VERSION, ENVIRONMENT_FOLDER
from .environment import EditorEnvironment, InstallError
from .process import EditorProcess, StartError
from .proxy import Sessions

_LOGGER = logging.getLogger(__name__)

# Waits before the next start after the editor ended or failed: longer each time in a row.
_RESTART_DELAYS = (5, 30, 120, 600)
# A run this long resets the waits.
_STABLE_RUN = 600.0


class EditorManager:
    """The editor of one config entry."""

    def __init__(self, hass: HomeAssistant, *, password: str, requirements: str) -> None:
        self.hass = hass
        self.password = password
        self._requirements = requirements
        self.sessions = Sessions()
        self.environment = EditorEnvironment(Path(hass.config.path(ENVIRONMENT_FOLDER)))
        self.process: EditorProcess | None = None
        # Increases with every start, so the proxy signs in again after a restart.
        self.run = 0
        self.status = "Starting"
        self.detail = f"XKNX Editor {EDITOR_VERSION}"
        # The editor and the proxy talk over the loopback interface; cookies stay with the proxy.
        self.client = aiohttp.ClientSession(
            cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False
        )
        self._stopping = False

    def _progress(self, step: str) -> None:
        self.status = step
        _LOGGER.info("XKNX Editor: %s", step)

    async def async_run(self) -> None:
        """Install, start and watch the editor until async_stop."""
        failures = 0
        while not self._stopping:
            started = asyncio.get_running_loop().time()
            try:
                await self._async_start()
                self.status, self.detail = "Running", ""
                code = await self.process.async_wait() if self.process else 0
                if self._stopping:
                    break
                self.status = "Failed: the editor ended"
                self.detail = f"Exit code {code}\n" + "\n".join(self.process.output)
                _LOGGER.warning("XKNX Editor ended with exit code %s", code)
            except (InstallError, StartError) as error:
                self.status, self.detail = "Failed to start", str(error)
                _LOGGER.error("XKNX Editor could not start: %s", error)
            if asyncio.get_running_loop().time() - started > _STABLE_RUN:
                failures = 0
            delay = _RESTART_DELAYS[min(failures, len(_RESTART_DELAYS) - 1)]
            failures += 1
            self.status += f" (next try in {delay} s)"
            await asyncio.sleep(delay)

    async def _async_start(self) -> None:
        self._progress(f"Installing XKNX Editor {EDITOR_VERSION}")
        requirements = await self.environment.async_requirements(
            async_get_clientsession(self.hass), self._requirements
        )
        python = await self.environment.async_ensure(requirements, self._progress)
        self._progress("Starting")
        self.process = EditorProcess(
            python,
            data=Path(self.hass.config.path(DATA_FOLDER)),
            files=Path(self.hass.config.path(DATA_FOLDER, "files")),
            cache=self.environment.root / "cache",
            password=self.password,
        )
        self.run += 1
        await self.process.async_start(self.client)

    async def async_stop(self) -> None:
        self._stopping = True
        if self.process is not None:
            await self.process.async_stop()
        await self.client.close()
