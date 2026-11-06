"""The link to the editor: the editor's project for Home Assistant's KNX integration, and what
the KNX integration knows for the editor.

While the editor runs, the integration signs in to it (as its proxy does) and keeps the websocket
``/host`` open (see ``editor_gui/web/host_link.py`` in the editor). It tells the editor
- the state of the KNX integration, the project it holds and its connection (``knx``),
- what Home Assistant does with each group address (``usage``),
at the start and whenever that changes, the last value of each group address once (``values``)
and every telegram of the KNX integration (``telegram``). It stores a project the editor sends as
the KNX integration's project and answers a request for the recorded telegrams
(``telegram_history``). An editor without the link (an older version) is asked again now and
then.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp import ClientTimeout, WSMsgType, hdrs
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant

from .areas import AreaLinks
from .const import EDITOR_COOKIE
from .entities import EntityLinks
from .knx_bridge import (
    KnxError,
    async_send_group,
    async_set_keyring,
    async_set_project,
    async_set_time_server,
    async_telegram_history,
    connection_info,
    group_address_usage,
    keyring_allowed,
    knx_loaded,
    latest_telegrams,
    project_info,
    subscribe_telegrams,
    time_server,
)

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
# The most hours of recorded telegrams the editor gets.
_MAX_HISTORY_HOURS = 24 * 31


class EditorLink:
    """Keeps the link to the editor of ``manager`` open while it runs."""

    def __init__(self, hass: HomeAssistant, manager: EditorManager, version: str) -> None:
        self.hass = hass
        self.manager = manager
        self.version = version
        # Whether the link is open now (diagnostics).
        self.connected = False
        # The floors and areas the editor's spaces became, the entities its functions became.
        self.areas = AreaLinks(hass)
        self.entities = EntityLinks(hass)

    def knx_state(self) -> dict[str, Any]:
        return {
            "type": "knx",
            "loaded": knx_loaded(self.hass),
            "project": project_info(self.hass),
            "connection": connection_info(self.hass),
            # Where its time server sends time and date to; why the editor's keys cannot be its.
            "time_server": time_server(self.hass),
            "keyring_refused": keyring_allowed(self.hass),
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
            # Everything goes out in order through one sender.
            outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

            async def send() -> None:
                while True:
                    await socket.send_json(await outbox.get())

            sender = asyncio.create_task(send())
            unsubscribe = subscribe_telegrams(
                self.hass, lambda t: outbox.put_nowait({"type": "telegram", "telegram": t})
            )
            try:
                await self._async_talk(socket, outbox, run)
            finally:
                unsubscribe()
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sender

    async def _async_talk(
        self,
        socket: aiohttp.ClientWebSocketResponse,
        outbox: asyncio.Queue[dict[str, Any]],
        run: int,
    ) -> None:
        outbox.put_nowait(
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
        used: dict[str, Any] | None = None
        places: dict[str, Any] | None = None
        made: dict[str, Any] | None = None
        await self.areas.async_load()
        await self.entities.async_load()

        def report() -> None:
            nonlocal reported, used, places, made
            state = self.knx_state()
            if state != reported:
                reported = state
                outbox.put_nowait(state)
            usage = group_address_usage(self.hass)
            if usage != used:
                used = usage
                outbox.put_nowait({"type": "usage", "group_addresses": usage})
            snapshot = self.areas.snapshot()
            if snapshot != places:
                places = snapshot
                outbox.put_nowait(snapshot)
            entity_links = self.entities.snapshot()
            if entity_links != made:
                made = entity_links
                outbox.put_nowait(entity_links)

        report()
        outbox.put_nowait({"type": "values", "telegrams": latest_telegrams(self.hass)})
        greeted = False
        while self.manager.run == run and not socket.closed:
            try:
                message = await socket.receive(timeout=_POLL_SECONDS)
            except TimeoutError:
                report()
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
            await self._async_handle(outbox, data, report)
            report()

    async def _async_handle(
        self, outbox: asyncio.Queue[dict[str, Any]], message: Any, report: Any
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
            report()
            outbox.put_nowait({"type": "result", "id": message.get("id"), "error": error})
        elif kind == "apply_areas":
            result: dict[str, Any] = {"type": "result", "id": message.get("id"), "error": None}
            try:
                result["changed"] = await self.areas.async_apply(
                    message.get("floors"), message.get("areas")
                )
            except ValueError as failure:
                result["error"] = str(failure)
            except Exception as failure:  # the editor shows why
                _LOGGER.exception("The floors and areas could not be applied")
                result["error"] = str(failure) or type(failure).__name__
            # The new floors and areas first, then the answer.
            report()
            outbox.put_nowait(result)
        elif kind in (
            "check_entities",
            "create_entities",
            "update_entities",
            "delete_entities",
            "set_time_server",
            "set_keyring",
        ):
            reply: dict[str, Any] = {"type": "result", "id": message.get("id"), "error": None}
            try:
                if kind == "check_entities":
                    reply["checks"] = self.entities.check(message.get("entities"))
                elif kind == "create_entities":
                    created, errors = await self.entities.async_create(message.get("entities"))
                    reply["created"], reply["errors"] = created, errors
                elif kind == "update_entities":
                    updated, errors = await self.entities.async_update(message.get("entities"))
                    reply["updated"], reply["errors"] = updated, errors
                elif kind == "delete_entities":
                    removed, errors = await self.entities.async_delete(message.get("keys"))
                    reply["removed"], reply["errors"] = removed, errors
                elif kind == "set_time_server":
                    await async_set_time_server(self.hass, message.get("addresses"))
                else:
                    await async_set_keyring(
                        self.hass,
                        base64.b64decode(str(message.get("keyring", "")), validate=True),
                        str(message.get("password", "")),
                    )
            except (KnxError, binascii.Error) as failure:
                reply["error"] = str(failure)
            except ValueError as failure:
                reply["error"] = str(failure)
            except Exception as failure:  # the editor shows why
                _LOGGER.exception("The entities could not be checked or created")
                reply["error"] = str(failure) or type(failure).__name__
            # The new entities first (their links and usage), then the answer.
            report()
            outbox.put_nowait(reply)
        elif kind == "group_send":
            # No answer: the telegram comes back as one of the KNX integration's.
            try:
                await async_send_group(self.hass, message)
            except Exception:  # a wrong value must not end the link
                _LOGGER.warning("The editor's group telegram was not sent", exc_info=True)
        elif kind == "telegram_history":
            answer: dict[str, Any] = {"type": "result", "id": message.get("id"), "error": None}
            try:
                hours = int(message.get("hours", 24))
                hours = max(1, min(hours, _MAX_HISTORY_HOURS))
                answer["telegrams"] = await async_telegram_history(self.hass, hours)
            except (KnxError, ValueError, TypeError) as failure:
                answer["error"] = str(failure)
            except Exception as failure:  # the editor shows why
                _LOGGER.exception("The KNX integration's telegrams could not be read")
                answer["error"] = str(failure) or type(failure).__name__
            outbox.put_nowait(answer)
