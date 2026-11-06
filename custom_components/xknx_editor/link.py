"""The link to the editor: the editor's project for Home Assistant's KNX integration.

While the editor runs, the integration signs in to it (as its proxy does) and keeps the websocket
``/host`` open (see ``editor_gui/web/host_link.py`` in the editor). It tells the editor the state
of the KNX integration and the project it holds, at the start and whenever that changes, and
stores a project the editor sends as the KNX integration's project (:mod:`.knx_bridge`). An
editor without the link (an older version) is asked again now and then.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp import ClientTimeout, WSMsgType, hdrs
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant

from .const import EDITOR_COOKIE
from .knx_bridge import KnxError, async_set_project, knx_loaded, project_info

if TYPE_CHECKING:
    from .manager import EditorManager
    from .process import EditorProcess

_LOGGER = logging.getLogger(__name__)

PROTOCOL = 1
# How often the KNX integration's state is looked at while the link is open.
_POLL_SECONDS = 5.0
# Waits before the next try while the editor does not run, or after the link failed.
_WAIT_SECONDS = 5.0
_RETRY_SECONDS = 60.0
_TIMEOUT = ClientTimeout(total=30)


class EditorLink:
    """Keeps the link to the editor of ``manager`` open while it runs."""

    def __init__(self, hass: HomeAssistant, manager: EditorManager, version: str) -> None:
        self.hass = hass
        self.manager = manager
        self.version = version
        # Whether the link is open now (diagnostics).
        self.connected = False

    def knx_state(self) -> dict[str, Any]:
        return {
            "type": "knx",
            "loaded": knx_loaded(self.hass),
            "project": project_info(self.hass),
        }

    async def async_run(self) -> None:
        """Until cancelled (the config entry unloads)."""
        while True:
            process = self.manager.process
            if self.manager.status != "Running" or process is None or not process.running:
                await asyncio.sleep(_WAIT_SECONDS)
                continue
            try:
                await self._async_link(process, self.manager.run)
                delay = _WAIT_SECONDS
            except (aiohttp.ClientError, TimeoutError, ValueError) as error:
                _LOGGER.debug("No link to the editor: %s", error)
                delay = _RETRY_SECONDS
            finally:
                self.connected = False
            await asyncio.sleep(delay)

    async def _async_cookie(self, process: EditorProcess) -> str:
        async with self.manager.client.post(
            process.url + "login",
            data={"password": self.manager.password},
            allow_redirects=False,
            timeout=_TIMEOUT,
        ) as response:
            morsel = response.cookies.get(EDITOR_COOKIE)
            if morsel is None:
                raise aiohttp.ClientError("the editor refused the sign-in")
            return morsel.value

    async def _async_link(self, process: EditorProcess, run: int) -> None:
        cookie = await self._async_cookie(process)
        async with self.manager.client.ws_connect(
            process.url + "host",
            headers={hdrs.COOKIE: f"{EDITOR_COOKIE}={cookie}"},
            # A project is megabytes.
            max_msg_size=0,
            heartbeat=30,
            timeout=aiohttp.ClientWSTimeout(ws_receive=None, ws_close=10),
        ) as socket:
            await socket.send_json(
                {
                    "type": "hello",
                    "protocol": PROTOCOL,
                    "integration": self.version,
                    "home_assistant": HA_VERSION,
                }
            )
            self.connected = True
            _LOGGER.debug("Linked to the editor")
            reported: dict[str, Any] | None = None

            async def report() -> None:
                nonlocal reported
                state = self.knx_state()
                if state != reported:
                    reported = state
                    await socket.send_json(state)

            await report()
            greeted = False
            while self.manager.run == run and not socket.closed:
                try:
                    message = await socket.receive(timeout=_POLL_SECONDS)
                except TimeoutError:
                    await report()
                    continue
                if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                    if not greeted:
                        # Not let in, or an editor without the link.
                        raise aiohttp.ClientError(f"the editor closed the link ({message.data})")
                    return
                if message.type is not WSMsgType.TEXT:
                    continue
                data = json.loads(message.data)
                greeted = greeted or (isinstance(data, dict) and data.get("type") == "hello")
                await self._async_handle(socket, data, report)
                await report()

    async def _async_handle(
        self, socket: aiohttp.ClientWebSocketResponse, message: Any, report: Any
    ) -> None:
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        if kind == "hello" and message.get("protocol") != PROTOCOL:
            _LOGGER.warning(
                "The editor speaks link protocol %s, this integration %s: update both",
                message.get("protocol"),
                PROTOCOL,
            )
        elif kind == "set_project":
            error: str | None = None
            try:
                await async_set_project(self.hass, message.get("project"))
            except KnxError as failure:
                error = str(failure)
            except Exception as failure:  # the editor shows why
                _LOGGER.exception("The KNX integration did not take the editor's project")
                error = str(failure) or type(failure).__name__
            # The new project first, then the answer (the editor links the project by it).
            await report()
            await socket.send_json({"type": "result", "id": message.get("id"), "error": error})
