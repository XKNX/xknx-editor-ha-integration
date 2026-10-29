"""The way from Home Assistant to the editor: sessions, sign-in, HTTP and websocket."""

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.typing import ClientSessionGenerator, WebSocketGenerator

from custom_components.xknx_editor import proxy
from custom_components.xknx_editor.const import (
    CONF_ALLOW_NON_ADMINS,
    CONF_EDITOR_PASSWORD,
    DOMAIN,
    EDITOR_COOKIE,
    MCP_URL,
    SESSION_COOKIE,
    UI_URL,
)
from custom_components.xknx_editor.manager import EditorManager
from custom_components.xknx_editor.proxy import Sessions

PASSWORD = "editor-password"


# Real sockets on 127.0.0.1: the editor's stand-in and the proxy's way to it.
pytestmark = pytest.mark.usefixtures("socket_enabled")


@dataclass
class FakeEditor:
    """The editor's web server: one sign-in password, one session per sign-in."""

    server: TestServer | None = None
    logins: int = 0
    seen: list[dict[str, str]] = field(default_factory=list)

    @property
    def url(self) -> str:
        assert self.server is not None
        return str(self.server.make_url("/"))

    running = True
    mcp_token = "editor-mcp-token"

    @property
    def mcp_endpoint(self) -> str:
        assert self.server is not None
        return str(self.server.make_url("/mcp"))

    async def mcp(self, request: web.Request) -> web.StreamResponse:
        """The editor's MCP server: its own token only; answers as an event stream in parts."""
        self.seen.append(dict(request.headers))
        if request.headers.get("Authorization") != f"Bearer {self.mcp_token}":
            return web.json_response({"error": "invalid_token"}, status=401)
        body = await request.read()
        response = web.StreamResponse(
            headers={"Content-Type": "text/event-stream", "Mcp-Session-Id": "s1"}
        )
        await response.prepare(request)
        await response.write(b"event: message\n")
        await response.write(b"data: " + (body or request.method.encode()) + b"\n\n")
        return response

    def _signed_in(self, request: web.Request) -> bool:
        return request.cookies.get(EDITOR_COOKIE, "").startswith("session-")

    async def login(self, request: web.Request) -> web.Response:
        form = await request.post()
        if form.get("password") != PASSWORD:
            return web.Response(status=200, text="login page")
        self.logins += 1
        response = web.Response(status=303, headers={"Location": "./"})
        response.set_cookie(EDITOR_COOKIE, f"session-{self.logins}")
        return response

    async def index(self, request: web.Request) -> web.Response:
        self.seen.append(dict(request.headers))
        if not self._signed_in(request):
            return web.Response(text="login page")
        response = web.Response(text="editor page", headers={"X-Frame-Options": "SAMEORIGIN"})
        response.set_cookie("other", "x")
        return response

    async def upload(self, request: web.Request) -> web.Response:
        body = await request.read()
        return web.json_response({"size": len(body), "query": request.query_string})

    async def socket(self, request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse()
        if not self._signed_in(request):
            await socket.prepare(request)
            await socket.close(code=4403)
            return socket
        await socket.prepare(request)
        async for message in socket:
            if message.type is WSMsgType.TEXT:
                await socket.send_str("echo " + message.data)
            elif message.type is WSMsgType.BINARY:
                await socket.send_bytes(message.data[::-1])
        return socket


@pytest.fixture
async def editor(hass: HomeAssistant) -> AsyncGenerator[FakeEditor]:
    fake = FakeEditor()
    app = web.Application()
    app.router.add_post("/login", fake.login)
    app.router.add_get("/", fake.index)
    app.router.add_post("/upload", fake.upload)
    app.router.add_get("/ws", fake.socket)
    app.router.add_route("*", "/mcp", fake.mcp)
    fake.server = TestServer(app)
    await fake.server.start_server()
    yield fake
    await fake.server.close()


@pytest.fixture
def options() -> dict[str, object]:
    """The config entry's options (a test sets others by parametrizing this)."""
    return {}


@pytest.fixture
async def manager(
    hass: HomeAssistant, editor: FakeEditor, options: dict[str, object]
) -> AsyncGenerator[EditorManager]:
    """The integration set up, with the fake editor running in place of the real one."""

    async def run(self: EditorManager) -> None:
        self.process = editor  # type: ignore[assignment]
        self.run = 1
        self.status = "Running"

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_EDITOR_PASSWORD: PASSWORD}, options=options)
    entry.add_to_hass(hass)
    with patch.object(EditorManager, "async_run", run):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
    manager = entry.runtime_data
    yield manager
    await manager.client.close()


async def session_for(hass_ws_client: WebSocketGenerator, hass: HomeAssistant) -> str:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/session"})
    response = await client.receive_json()
    assert response["success"]
    return response["result"]["session"]


async def test_without_session_nothing_reaches_the_editor(
    hass: HomeAssistant,
    manager: EditorManager,
    editor: FakeEditor,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    client = await hass_client_no_auth()
    response = await client.get(UI_URL)
    assert response.status == 401
    response = await client.get(UI_URL, cookies={SESSION_COOKIE: "made-up"})
    assert response.status == 401
    assert editor.seen == []


async def test_page_through_the_proxy(
    hass: HomeAssistant,
    manager: EditorManager,
    editor: FakeEditor,
    hass_client_no_auth: ClientSessionGenerator,
    hass_ws_client: WebSocketGenerator,
) -> None:
    session = await session_for(hass_ws_client, hass)
    client = await hass_client_no_auth()
    response = await client.get(UI_URL, cookies={SESSION_COOKIE: session, "ha_other": "secret"})
    assert response.status == 200
    assert await response.text() == "editor page"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    # The editor's cookies stay with the proxy.
    assert "Set-Cookie" not in response.headers
    headers = editor.seen[-1]
    # Only the editor's session goes to the editor, none of the browser's cookies.
    assert headers["Cookie"] == f"{EDITOR_COOKIE}=session-1"
    assert headers["Host"].startswith("127.0.0.1:")
    assert "X-Forwarded-Host" in headers
    # Signed in once per session.
    await client.get(UI_URL, cookies={SESSION_COOKIE: session})
    assert editor.logins == 1
    # Another session (another browser) signs in on its own.
    other = await session_for(hass_ws_client, hass)
    await client.get(UI_URL, cookies={SESSION_COOKIE: other})
    assert editor.logins == 2
    # A new editor run needs a new sign-in.
    manager.run += 1
    await client.get(UI_URL, cookies={SESSION_COOKIE: session})
    assert editor.logins == 3


async def test_upload_through_the_proxy(
    hass: HomeAssistant,
    manager: EditorManager,
    hass_client_no_auth: ClientSessionGenerator,
    hass_ws_client: WebSocketGenerator,
) -> None:
    session = await session_for(hass_ws_client, hass)
    client = await hass_client_no_auth()
    response = await client.post(
        UI_URL + "upload?dialog=1&name=a.knxproj",
        data=b"x" * 300_000,
        cookies={SESSION_COOKIE: session},
    )
    assert await response.json() == {"size": 300_000, "query": "dialog=1&name=a.knxproj"}


async def test_websocket_through_the_proxy(
    hass: HomeAssistant,
    manager: EditorManager,
    hass_client_no_auth: ClientSessionGenerator,
    hass_ws_client: WebSocketGenerator,
) -> None:
    session = await session_for(hass_ws_client, hass)
    client = await hass_client_no_auth()
    async with client.ws_connect(
        UI_URL + "ws", headers={"Cookie": f"{SESSION_COOKIE}={session}"}
    ) as socket:
        await socket.send_str("frame")
        assert await socket.receive_str() == "echo frame"
        await socket.send_bytes(b"abc")
        assert await socket.receive_bytes() == b"cba"


async def test_status_page_while_the_editor_is_not_running(
    hass: HomeAssistant,
    manager: EditorManager,
    hass_client_no_auth: ClientSessionGenerator,
    hass_ws_client: WebSocketGenerator,
) -> None:
    session = await session_for(hass_ws_client, hass)
    manager.process = None
    manager.status, manager.detail = "Installing XKNX Editor 0.9.0", "<step>"
    client = await hass_client_no_auth()
    response = await client.get(UI_URL, cookies={SESSION_COOKIE: session})
    assert response.status == 503
    text = await response.text()
    assert "Installing XKNX Editor 0.9.0" in text
    assert "&lt;step&gt;" in text


async def test_sessions_only_for_administrators(
    hass: HomeAssistant,
    manager: EditorManager,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id({"type": f"{DOMAIN}/session"})
    response = await client.receive_json()
    assert not response["success"]
    assert response["error"]["code"] == "unauthorized"
    await client.send_json_auto_id({"type": f"{DOMAIN}/session/validate", "session": "x"})
    assert (await client.receive_json())["error"]["code"] == "unauthorized"


@pytest.mark.parametrize("options", [{CONF_ALLOW_NON_ADMINS: True}])
async def test_every_user_with_the_option(
    hass: HomeAssistant,
    manager: EditorManager,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    assert not hass.data["frontend_panels"]["xknx-editor"].require_admin
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id({"type": f"{DOMAIN}/session"})
    response = await client.receive_json()
    assert response["success"]
    session = response["result"]["session"]
    await client.send_json_auto_id({"type": f"{DOMAIN}/session/validate", "session": session})
    assert (await client.receive_json())["result"] == {"valid": True}


async def test_validate_keeps_a_session_live(
    hass: HomeAssistant, manager: EditorManager, hass_ws_client: WebSocketGenerator
) -> None:
    session = await session_for(hass_ws_client, hass)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/session/validate", "session": session})
    assert (await client.receive_json())["result"] == {"valid": True}
    await client.send_json_auto_id({"type": f"{DOMAIN}/session/validate", "session": "x"})
    assert (await client.receive_json())["result"] == {"valid": False}


def test_sessions_end_without_renewal() -> None:
    now = [1000.0]
    sessions = Sessions()
    with patch.object(proxy.time, "monotonic", lambda: now[0]):
        token = sessions.create()
        now[0] += proxy.SESSION_TIMEOUT - 1
        assert sessions.validate(token)
        # Renewed: live for another full period.
        now[0] += proxy.SESSION_TIMEOUT - 1
        assert sessions.get(token) is not None
        now[0] += 2
        assert sessions.get(token) is None
        assert not sessions.validate(token)


async def test_panel_while_loaded(hass: HomeAssistant, manager: EditorManager) -> None:
    panels = hass.data["frontend_panels"]
    assert panels["xknx-editor"].require_admin
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    with patch.object(EditorManager, "async_stop") as stop:
        assert await hass.config_entries.async_unload(entry.entry_id)
    stop.assert_awaited_once()
    assert "xknx-editor" not in panels


async def test_mcp_through_home_assistant(
    hass: HomeAssistant,
    manager: EditorManager,
    editor: FakeEditor,
    hass_client: ClientSessionGenerator,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    # Without a Home Assistant token, Home Assistant answers itself.
    client = await hass_client_no_auth()
    assert (await client.post(MCP_URL, data=b"{}")).status == 401
    assert editor.seen == []

    client = await hass_client()
    response = await client.post(
        MCP_URL, data=b'{"method":"initialize"}', headers={"Accept": "text/event-stream"}
    )
    assert response.status == 200
    assert response.headers["Mcp-Session-Id"] == "s1"
    assert await response.text() == 'event: message\ndata: {"method":"initialize"}\n\n'
    # The editor gets its own token, not the Home Assistant token.
    assert editor.seen[-1]["Authorization"] == f"Bearer {editor.mcp_token}"
    assert editor.seen[-1]["Host"].startswith("127.0.0.1:")
    # Ending the MCP session.
    response = await client.delete(MCP_URL, headers={"Mcp-Session-Id": "s1"})
    assert await response.text() == "event: message\ndata: DELETE\n\n"
    assert editor.seen[-1]["Mcp-Session-Id"] == "s1"


async def test_mcp_only_for_administrators(
    hass: HomeAssistant,
    manager: EditorManager,
    editor: FakeEditor,
    hass_client: ClientSessionGenerator,
    hass_read_only_access_token: str,
) -> None:
    client = await hass_client(hass_read_only_access_token)
    response = await client.post(MCP_URL, data=b"{}")
    assert response.status == 403
    assert editor.seen == []


@pytest.mark.parametrize("options", [{CONF_ALLOW_NON_ADMINS: True}])
async def test_mcp_for_every_user_with_the_option(
    hass: HomeAssistant,
    manager: EditorManager,
    editor: FakeEditor,
    hass_client: ClientSessionGenerator,
    hass_read_only_access_token: str,
) -> None:
    client = await hass_client(hass_read_only_access_token)
    response = await client.post(MCP_URL, data=b'{"method":"tools/list"}')
    assert response.status == 200
    assert await response.text() == 'event: message\ndata: {"method":"tools/list"}\n\n'
    assert editor.seen[-1]["Authorization"] == f"Bearer {editor.mcp_token}"


async def test_mcp_without_the_integration(
    hass: HomeAssistant, manager: EditorManager, hass_client: ClientSessionGenerator
) -> None:
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    with patch.object(EditorManager, "async_stop"):
        assert await hass.config_entries.async_unload(entry.entry_id)
    client = await hass_client()
    response = await client.post(MCP_URL, data=b"{}")
    assert response.status == 503
    assert await response.json() == {"message": "XKNX Editor is not set up"}


async def test_mcp_while_the_editor_is_not_running(
    hass: HomeAssistant, manager: EditorManager, hass_client: ClientSessionGenerator
) -> None:
    manager.process = None
    client = await hass_client()
    response = await client.post(MCP_URL, data=b"{}")
    assert response.status == 503
    assert await response.json() == {"message": "XKNX Editor is not running"}
