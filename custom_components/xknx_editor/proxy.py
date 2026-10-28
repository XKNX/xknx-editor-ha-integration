"""Home Assistant's way to the editor: signed-in users reach its web UI below UI_URL.

Like the Ingress of Home Assistant apps, the panel asks for a session over Home Assistant's
websocket (administrators get one, every user when the options allow it) and stores it in a
cookie valid below URL_BASE. The proxy answers only requests with a live session, and forwards
them to the editor signed in with a session of the editor's own per browser session. The editor
itself accepts no other connections.

MCP clients reach the editor's MCP server at MCP_URL, signed in with a long-lived access token of
a Home Assistant administrator (of every user when the options allow it).
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import secrets
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

import aiohttp
from aiohttp import ClientTimeout, hdrs, web
from homeassistant.components.http import KEY_HASS_USER, HomeAssistantView
from multidict import CIMultiDict
from yarl import URL

from .const import EDITOR_COOKIE, MCP_URL, SESSION_COOKIE, SESSION_TIMEOUT, UI_URL

if TYPE_CHECKING:
    from .manager import EditorManager

_LOGGER = logging.getLogger(__name__)

# Request headers the proxy sets itself or leaves out.
_REQUEST_HEADERS_FILTER = {
    hdrs.HOST,
    hdrs.COOKIE,
    hdrs.AUTHORIZATION,
    hdrs.CONTENT_LENGTH,
    hdrs.CONTENT_ENCODING,
    hdrs.TRANSFER_ENCODING,
    hdrs.ACCEPT_ENCODING,
    hdrs.CONNECTION,
    hdrs.UPGRADE,
    hdrs.SEC_WEBSOCKET_EXTENSIONS,
    hdrs.SEC_WEBSOCKET_PROTOCOL,
    hdrs.SEC_WEBSOCKET_VERSION,
    hdrs.SEC_WEBSOCKET_KEY,
}
# Response headers aiohttp sets again; the editor's cookies stay with the proxy.
_RESPONSE_HEADERS_FILTER = {
    hdrs.TRANSFER_ENCODING,
    hdrs.CONTENT_LENGTH,
    hdrs.CONTENT_ENCODING,
    hdrs.CONNECTION,
    hdrs.SET_COOKIE,
}
_MAX_WEBSOCKET_MESSAGE = 64 * 1024 * 1024
_NO_TIMEOUT = ClientTimeout(total=None)
_LOGIN_TIMEOUT = ClientTimeout(total=30)


@dataclass
class _Session:
    expires: float
    # The editor's session for this one, and the editor run it belongs to.
    editor_cookie: str | None = None
    editor_run: int = -1


class Sessions:
    """The live sessions of the panel."""

    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}

    def create(self) -> str:
        self._expire()
        token = secrets.token_urlsafe(32)
        self._sessions[token] = _Session(time.monotonic() + SESSION_TIMEOUT)
        return token

    def validate(self, token: str) -> bool:
        """Whether ``token`` is live; it stays live for another SESSION_TIMEOUT then."""
        self._expire()
        session = self._sessions.get(token)
        if session is None:
            return False
        session.expires = time.monotonic() + SESSION_TIMEOUT
        return True

    def get(self, token: str | None) -> _Session | None:
        self._expire()
        return self._sessions.get(token) if token else None

    def _expire(self) -> None:
        now = time.monotonic()
        for token in [t for t, s in self._sessions.items() if s.expires < now]:
            del self._sessions[token]


class EditorProxyView(HomeAssistantView):
    """The editor's web UI below UI_URL."""

    name = "api:xknx_editor:ui"
    url = UI_URL + "{path:.*}"
    # Signed in with the session cookie (see the module docstring): the browser loads the page
    # in an iframe, which sends no Home Assistant token.
    requires_auth = False

    def __init__(self, managers: Callable[[], EditorManager | None]) -> None:
        # The view stays registered for the lifetime of Home Assistant; the editor of the
        # current config entry, if any.
        self._managers = managers

    async def _handle(self, request: web.Request, path: str) -> web.StreamResponse:
        manager = self._managers()
        session = manager.sessions.get(request.cookies.get(SESSION_COOKIE)) if manager else None
        if manager is None or session is None:
            return web.Response(status=401, text="Open XKNX Editor from the sidebar.")
        process = manager.process
        if process is None or not process.running:
            return _status_page(manager.status, manager.detail)
        try:
            cookie = await _editor_cookie(manager, session)
            url = URL(process.url).join(URL(quote(path)))
            if request.query_string:
                url = url.with_query(request.query_string)
            headers = _request_headers(request, cookie)
            if _is_websocket(request):
                return await _websocket(manager, request, url, headers)
            return await _request(manager, request, url, headers)
        except aiohttp.ClientError as error:
            _LOGGER.debug("Proxy error for %s: %s", path, error)
            return _status_page(manager.status, manager.detail)

    get = _handle
    post = _handle
    head = _handle


class McpProxyView(HomeAssistantView):
    """The editor's MCP server at MCP_URL, for MCP clients signed in with a long-lived access token
    of a Home Assistant administrator, or of every user when the option allow_non_admins opens the
    editor to all (Home Assistant checks the token). The proxy signs in to the editor's MCP server
    with the token of the current editor run instead."""

    name = "api:xknx_editor:mcp"
    url = MCP_URL
    requires_auth = True

    def __init__(self, managers: Callable[[], EditorManager | None]) -> None:
        self._managers = managers

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        manager = self._managers()
        if manager is None:
            return web.json_response({"message": "XKNX Editor is not set up"}, status=503)
        if not (request[KEY_HASS_USER].is_admin or manager.allow_non_admins):
            return web.json_response({"message": "Administrators only"}, status=403)
        process = manager.process
        if process is None or not process.running:
            return web.json_response({"message": "XKNX Editor is not running"}, status=503)
        url = URL(process.mcp_endpoint)
        if request.query_string:
            url = url.with_query(request.query_string)
        headers = CIMultiDict(
            (name, value)
            for name, value in request.headers.items()
            if name not in _REQUEST_HEADERS_FILTER
        )
        headers[hdrs.AUTHORIZATION] = f"Bearer {process.mcp_token}"
        try:
            return await _request(manager, request, url, headers)
        except aiohttp.ClientError as error:
            _LOGGER.debug("MCP proxy error: %s", error)
            return web.json_response({"message": "XKNX Editor did not answer"}, status=502)

    get = _handle
    post = _handle
    delete = _handle


async def _editor_cookie(manager: EditorManager, session: _Session) -> str:
    """The editor's session for ``session``, signed in once per editor run."""
    run = manager.run
    if session.editor_cookie is not None and session.editor_run == run:
        return session.editor_cookie
    process = manager.process
    assert process is not None
    async with manager.client.post(
        process.url + "login",
        data={"password": manager.password},
        allow_redirects=False,
        timeout=_LOGIN_TIMEOUT,
    ) as response:
        morsel = response.cookies.get(EDITOR_COOKIE)
        if morsel is None:
            raise aiohttp.ClientError("the editor refused the sign-in")
        session.editor_cookie = morsel.value
        session.editor_run = run
        return morsel.value


async def _request(
    manager: EditorManager, request: web.Request, url: URL, headers: CIMultiDict[str]
) -> web.StreamResponse:
    async with manager.client.request(
        request.method,
        url,
        headers=headers,
        allow_redirects=False,
        data=request.content if request.body_exists else None,
        timeout=_NO_TIMEOUT,
        skip_auto_headers={hdrs.CONTENT_TYPE},
    ) as result:
        response = web.StreamResponse(
            status=result.status,
            headers=CIMultiDict(
                (name, value)
                for name, value in result.headers.items()
                if name not in _RESPONSE_HEADERS_FILTER
            ),
        )
        await response.prepare(request)
        with contextlib.suppress(aiohttp.ClientPayloadError, ConnectionError):
            async for data, _ in result.content.iter_chunks():
                await response.write(data)
        return response


async def _websocket(
    manager: EditorManager, request: web.Request, url: URL, headers: CIMultiDict[str]
) -> web.WebSocketResponse:
    protocols = _protocols(request)
    server = web.WebSocketResponse(
        protocols=protocols,
        autoclose=False,
        autoping=False,
        max_msg_size=_MAX_WEBSOCKET_MESSAGE,
    )
    await server.prepare(request)
    async with manager.client.ws_connect(
        url,
        headers=headers,
        protocols=protocols,
        autoclose=False,
        autoping=False,
        max_msg_size=_MAX_WEBSOCKET_MESSAGE,
    ) as client:
        forwards = [
            asyncio.create_task(_forward(server, client)),
            asyncio.create_task(_forward(client, server)),
        ]
        _done, pending = await asyncio.wait(forwards, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if not server.closed:
            await server.close(code=client.close_code or 1000)
    return server


def _is_websocket(request: web.Request) -> bool:
    return (
        "upgrade" in request.headers.get(hdrs.CONNECTION, "").lower()
        and request.headers.get(hdrs.UPGRADE, "").lower() == "websocket"
    )


def _protocols(request: web.Request) -> Iterable[str]:
    value = request.headers.get(hdrs.SEC_WEBSOCKET_PROTOCOL, "")
    return [p.strip() for p in value.split(",") if p.strip()]


def _request_headers(request: web.Request, editor_cookie: str) -> CIMultiDict[str]:
    """The browser's headers for the editor: with the editor's session instead of the browser's
    cookies, and the browser's host and scheme for the editor's same-origin check."""
    headers = CIMultiDict(
        (name, value)
        for name, value in request.headers.items()
        if name not in _REQUEST_HEADERS_FILTER
    )
    headers[hdrs.COOKIE] = f"{EDITOR_COOKIE}={editor_cookie}"
    headers[hdrs.X_FORWARDED_HOST] = request.headers.get(hdrs.X_FORWARDED_HOST, request.host)
    headers[hdrs.X_FORWARDED_PROTO] = request.headers.get(hdrs.X_FORWARDED_PROTO, request.scheme)
    return headers


async def _forward(
    source: web.WebSocketResponse | aiohttp.ClientWebSocketResponse,
    target: web.WebSocketResponse | aiohttp.ClientWebSocketResponse,
) -> None:
    with contextlib.suppress(RuntimeError, ConnectionError):
        async for message in source:
            if message.type is aiohttp.WSMsgType.TEXT:
                await target.send_str(message.data)
            elif message.type is aiohttp.WSMsgType.BINARY:
                await target.send_bytes(message.data)
            elif message.type is aiohttp.WSMsgType.PING:
                await target.ping(message.data)
            elif message.type is aiohttp.WSMsgType.PONG:
                await target.pong(message.data)
    if not target.closed:
        await target.close(code=source.close_code or 1000)


_STATUS_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta http-equiv="refresh" content="{refresh}"><title>XKNX Editor</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2em; color: #212121; background: #fafafa; }}
@media (prefers-color-scheme: dark) {{ body {{ color: #e1e1e1; background: #111; }} }}
pre {{ white-space: pre-wrap; font-size: 0.85em; }}
</style></head>
<body><h2>{title}</h2><pre>{detail}</pre></body></html>
"""


def _status_page(status: str, detail: str) -> web.Response:
    """What the panel shows while the editor is not running (yet)."""
    failed = status.startswith("Failed")
    return web.Response(
        status=503,
        content_type="text/html",
        headers={hdrs.CACHE_CONTROL: "no-store"},
        text=_STATUS_PAGE.format(
            refresh=30 if failed else 3,
            title=html.escape(status),
            detail=html.escape(detail),
        ),
    )
