"""The link to the editor: sign-in, the KNX integration's state, the editor's project."""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.xknx_editor import link as link_module
from custom_components.xknx_editor.const import EDITOR_COOKIE
from custom_components.xknx_editor.knx_bridge import KnxError
from custom_components.xknx_editor.link import PROTOCOL, EditorLink

PASSWORD = "editor-password"
# What the state of the KNX integration says while there is none.
_WITHOUT_KNX = {
    "connection": None,
    "time_server": None,
    "keyring_refused": "the KNX integration is not set up",
}

pytestmark = pytest.mark.usefixtures("socket_enabled")


class _Editor:
    """The editor's sign-in and its ``/host`` websocket, driven by the test."""

    def __init__(self) -> None:
        self.server: TestServer | None = None
        self.running = True
        self.received: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.socket: web.WebSocketResponse | None = None
        self.connections = 0
        self.refuse = False

    @property
    def url(self) -> str:
        assert self.server is not None
        return str(self.server.make_url("/"))

    async def login(self, request: web.Request) -> web.Response:
        form = await request.post()
        if form.get("password") != PASSWORD:
            return web.Response(text="login page")
        response = web.Response(status=303, headers={"Location": "./"})
        response.set_cookie(EDITOR_COOKIE, "session-1")
        return response

    async def host(self, request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        if self.refuse or request.cookies.get(EDITOR_COOKIE) != "session-1":
            await socket.close(code=4403)
            return socket
        self.connections += 1
        self.socket = socket
        await socket.send_json({"type": "hello", "protocol": PROTOCOL, "editor": "0.9.12"})
        async for message in socket:
            if message.type is WSMsgType.TEXT:
                await self.received.put(message.json())
        return socket

    async def next(self, kind: str) -> dict[str, Any]:
        while True:
            message = await asyncio.wait_for(self.received.get(), 10)
            if message["type"] == kind:
                return message


class _Manager:
    def __init__(self, editor: _Editor, client: aiohttp.ClientSession) -> None:
        self.status = "Running"
        self.process = editor
        self.run = 1
        self.password = PASSWORD
        self.client = client


@pytest.fixture
async def editor() -> AsyncGenerator[_Editor]:
    fake = _Editor()
    app = web.Application()
    app.router.add_post("/login", fake.login)
    app.router.add_get("/host", fake.host)
    fake.server = TestServer(app, host="127.0.0.1")
    await fake.server.start_server()
    yield fake
    await fake.server.close()


@pytest.fixture
async def linked(
    hass: HomeAssistant, editor: _Editor
) -> AsyncGenerator[tuple[EditorLink, _Manager]]:
    client = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
    manager = _Manager(editor, client)
    link = EditorLink(hass, manager, "0.9.12")  # type: ignore[arg-type]
    with patch.object(link_module, "_POLL_SECONDS", 0.05):
        task = asyncio.create_task(link.async_run())
        yield link, manager
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await client.close()


async def test_the_link_reports_the_knx_integration(
    hass: HomeAssistant, editor: _Editor, linked: tuple[EditorLink, _Manager]
) -> None:
    link, _manager = linked
    hello = await editor.next("hello")
    assert hello["protocol"] == PROTOCOL
    assert hello["integration"] == "0.9.12"
    assert hello["home_assistant"]
    assert await editor.next("knx") == {
        "type": "knx",
        "loaded": False,
        "project": None,
        **_WITHOUT_KNX,
    }
    assert link.connected

    # The KNX integration is set up and gets a project in its panel: reported on its own.
    held = {"name": "House", "guid": "g", "tool_version": "t", "created_by": "c"}
    with (
        patch.object(link_module, "knx_loaded", return_value=True),
        patch.object(link_module, "project_info", return_value=held),
    ):
        assert await editor.next("knx") == {
            "type": "knx",
            "loaded": True,
            "project": held,
            **_WITHOUT_KNX,
        }


async def test_the_editors_project_goes_to_the_knx_integration(
    hass: HomeAssistant, editor: _Editor, linked: tuple[EditorLink, _Manager]
) -> None:
    await editor.next("knx")
    stored: list[Any] = []
    held = {"name": "House", "guid": "guid-1", "created_by": "XKNX Editor"}

    async def store(_hass: HomeAssistant, project: Any) -> None:
        stored.append(project)

    with (
        patch.object(link_module, "async_set_project", store),
        patch.object(link_module, "knx_loaded", return_value=True),
        patch.object(link_module, "project_info", side_effect=lambda _h: held if stored else None),
    ):
        assert editor.socket is not None
        await editor.socket.send_json({"type": "set_project", "id": 7, "project": {"info": {}}})
        # The new project first, then the answer (a poll may report the KNX integration as
        # loaded before).
        while (state := await editor.next("knx"))["project"] is None:
            assert state["loaded"] is True
        assert state == {"type": "knx", "loaded": True, "project": held, **_WITHOUT_KNX}
        assert await editor.next("result") == {"type": "result", "id": 7, "error": None}
    assert stored == [{"info": {}}]


async def test_a_refused_project_is_answered_with_the_reason(
    hass: HomeAssistant, editor: _Editor, linked: tuple[EditorLink, _Manager]
) -> None:
    await editor.next("knx")

    async def refuse(_hass: HomeAssistant, _project: Any) -> None:
        raise KnxError("the KNX integration is not set up")

    async def crash(_hass: HomeAssistant, _project: Any) -> None:
        raise RuntimeError("boom")

    assert editor.socket is not None
    with patch.object(link_module, "async_set_project", refuse):
        await editor.socket.send_json({"type": "set_project", "id": 1, "project": {}})
        result = await editor.next("result")
    assert result == {"type": "result", "id": 1, "error": "the KNX integration is not set up"}
    with patch.object(link_module, "async_set_project", crash):
        await editor.socket.send_json({"type": "set_project", "id": 2, "project": {}})
        result = await editor.next("result")
    assert result == {"type": "result", "id": 2, "error": "boom"}
    # Neither ends the link.
    await editor.socket.send_json({"type": "hello", "protocol": PROTOCOL + 1})
    await editor.socket.send_str("[]")
    await editor.socket.send_bytes(b"x")
    await asyncio.sleep(0.2)
    assert editor.connections == 1


async def test_a_new_editor_run_links_again(
    hass: HomeAssistant, editor: _Editor, linked: tuple[EditorLink, _Manager]
) -> None:
    _link, manager = linked
    await editor.next("knx")
    with patch.object(link_module, "_WAIT_SECONDS", 0.05):
        manager.run += 1
        await editor.next("hello")
    assert editor.connections == 2


async def test_no_link_while_the_editor_does_not_run(hass: HomeAssistant, editor: _Editor) -> None:
    client = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
    manager = _Manager(editor, client)
    manager.status = "Starting"
    link = EditorLink(hass, manager, "0.9.12")  # type: ignore[arg-type]
    with patch.object(link_module, "_WAIT_SECONDS", 0.05):
        task = asyncio.create_task(link.async_run())
        await asyncio.sleep(0.2)
        assert editor.connections == 0
        manager.status = "Running"
        await editor.next("hello")
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await client.close()


async def test_an_editor_without_the_link_is_asked_again_later(
    hass: HomeAssistant, editor: _Editor
) -> None:
    editor.refuse = True
    client = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
    manager = _Manager(editor, client)
    manager.password = "wrong"
    link = EditorLink(hass, manager, "0.9.12")  # type: ignore[arg-type]
    with patch.object(link_module, "_RETRY_SECONDS", 0.05):
        task = asyncio.create_task(link.async_run())
        await asyncio.sleep(0.3)
        assert not link.connected
        # Signed in now, and the editor has the link.
        manager.password = PASSWORD
        editor.refuse = False
        await editor.next("hello")
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await client.close()


async def test_what_the_knx_integration_knows_reaches_the_editor(
    hass: HomeAssistant, editor: _Editor, linked: tuple[EditorLink, _Manager]
) -> None:
    # Before the KNX integration is set up: nothing used, no values.
    assert await editor.next("usage") == {"type": "usage", "group_addresses": {}}
    assert await editor.next("values") == {"type": "values", "telegrams": []}
    used = {"2563": {"users": [{"kind": "expose", "name": "sun"}], "sends": True}}
    with patch.object(link_module, "group_address_usage", return_value=used):
        assert await editor.next("usage") == {"type": "usage", "group_addresses": used}
    # Every telegram of the KNX integration, as it has it.
    telegram = {"destination": "1/2/3", "payload": 1, "telegramtype": "GroupValueWrite"}
    async_dispatcher_send(hass, "knx_telegram", object(), telegram)
    assert await editor.next("telegram") == {"type": "telegram", "telegram": telegram}


async def test_the_recorded_telegrams_are_given(
    hass: HomeAssistant, editor: _Editor, linked: tuple[EditorLink, _Manager]
) -> None:
    await editor.next("knx")
    asked: list[int] = []

    async def history(_hass: HomeAssistant, hours: int) -> list[dict[str, Any]]:
        asked.append(hours)
        return [{"destination": "1/2/3"}]

    async def not_ready(_hass: HomeAssistant, _hours: int) -> list[dict[str, Any]]:
        raise KnxError("the KNX integration's telegram history is not ready")

    assert editor.socket is not None
    with patch.object(link_module, "async_telegram_history", history):
        await editor.socket.send_json({"type": "telegram_history", "id": 3, "hours": 6})
        assert await editor.next("result") == {
            "type": "result",
            "id": 3,
            "error": None,
            "telegrams": [{"destination": "1/2/3"}],
        }
        # Kept within a month.
        await editor.socket.send_json({"type": "telegram_history", "id": 4, "hours": 99999})
        await editor.next("result")
    assert asked == [6, 24 * 31]
    with patch.object(link_module, "async_telegram_history", not_ready):
        await editor.socket.send_json({"type": "telegram_history", "id": 5})
        result = await editor.next("result")
    assert result["error"] == "the KNX integration's telegram history is not ready"
    await editor.socket.send_json({"type": "telegram_history", "id": 6, "hours": "x"})
    assert (await editor.next("result"))["error"]


async def test_floors_and_areas_for_the_editor(
    hass: HomeAssistant, editor: _Editor, linked: tuple[EditorLink, _Manager]
) -> None:
    first = await editor.next("areas")
    assert first == {"type": "areas", "floors": [], "areas": [], "links": {}}
    assert editor.socket is not None
    await editor.socket.send_json(
        {
            "type": "apply_areas",
            "id": 9,
            "floors": [{"key": "g:2", "name": "Ground Floor", "level": 0}],
            "areas": [{"key": "g:3", "name": "Kitchen", "floor_key": "g:2"}],
        }
    )
    # The new floors and areas first, then the answer.
    snapshot = await editor.next("areas")
    assert [f["name"] for f in snapshot["floors"]] == ["Ground Floor"]
    assert [a["name"] for a in snapshot["areas"]] == ["Kitchen"]
    assert set(snapshot["links"]) == {"g:2", "g:3"}
    assert await editor.next("result") == {"type": "result", "id": 9, "error": None, "changed": 2}

    await editor.socket.send_json({"type": "apply_areas", "id": 10, "floors": "x", "areas": []})
    result = await editor.next("result")
    assert result["id"] == 10 and result["error"]


async def test_entities_are_checked_and_created_for_the_editor(
    hass: HomeAssistant, editor: _Editor, linked: tuple[EditorLink, _Manager]
) -> None:
    link, _manager = linked
    assert await editor.next("entity_links") == {
        "type": "entity_links",
        "links": {},
        "configs": {},
    }
    candidate = {"key": "g:function:1", "platform": "light", "name": "Ceiling", "knx": {}}

    async def create(_entities: Any) -> tuple[dict[str, str], dict[str, str]]:
        return {"g:function:1": "light.ceiling"}, {}

    assert editor.socket is not None
    with (
        patch.object(link.entities, "check", return_value={"g:function:1": None}),
        patch.object(link.entities, "async_create", create),
    ):
        await editor.socket.send_json({"type": "check_entities", "id": 4, "entities": [candidate]})
        assert await editor.next("result") == {
            "type": "result",
            "id": 4,
            "error": None,
            "checks": {"g:function:1": None},
        }
        await editor.socket.send_json({"type": "create_entities", "id": 5, "entities": [candidate]})
        assert await editor.next("result") == {
            "type": "result",
            "id": 5,
            "error": None,
            "created": {"g:function:1": "light.ceiling"},
            "errors": {},
        }
    await editor.socket.send_json({"type": "check_entities", "id": 6, "entities": "x"})
    result = await editor.next("result")
    assert result["id"] == 6 and result["error"]
