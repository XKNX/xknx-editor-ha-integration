# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository. `CLAUDE.md` only refers to this file, so keep all instructions here.

## What this is

A Home Assistant custom integration (domain `xknx_editor`) that runs the [XKNX Editor](https://github.com/XKNX/xknx-editor) in its browser mode and shows it in Home Assistant's sidebar. It is published through the Home Assistant Marketplace (HACS), which needs one integration per repository in `custom_components/<domain>/`.

## Architecture

- **Two parts.** The integration itself runs inside Home Assistant and has no `requirements`: it only uses what Home Assistant ships (aiohttp, voluptuous, ...). The editor runs as a process of its own (`process.py`), with its own frame loop, threads and packages.
- **The editor's environment** (`environment.py`): a virtual environment with Home Assistant's Python, created and filled with uv (`python -m uv`, which Home Assistant installs its own packages with) from the requirements file of an editor release (`const.REQUIREMENTS_URL`, version `const.EDITOR_VERSION`). So the editor gets exactly the package versions it was released with, and Home Assistant's packages stay untouched. `--no-build`: every package must come as a wheel (Home Assistant's image is Alpine Linux, so musllinux wheels for CPython of Home Assistant's version, x86_64 and aarch64). The environment is created again when Home Assistant's Python version changes.
- **Folders:** the environment, downloads and the editor's cache go to `/config/.cache/xknx_editor` (Home Assistant leaves `.cache` out of its backups); the editor's settings, catalog and file folder go to `/config/xknx_editor` (kept in backups).
- **Access** (`proxy.py`, `frontend/panel.js`): like the Ingress of Home Assistant apps. The panel (administrators only, unless the option `allow_non_admins` opens panel and sessions to every signed-in user) gets a session over Home Assistant's websocket (`xknx_editor/session`, renewed with `xknx_editor/session/validate`) and stores it in the cookie `xknx_editor_session` for `/api/xknx_editor/`. One session per browser tab (kept in `sessionStorage` while it is live): Home Assistant may create the panel more than once, and the editor gives control back only to a returning session. The proxy view at `/api/xknx_editor/ui/` forwards HTTP and websocket requests with a live session to the editor on `127.0.0.1`. The editor requires a password (random, in the config entry); only the proxy signs in with it, once per panel session and editor run, and sends the editor's session cookie instead of the browser's cookies.
- **MCP** (`proxy.McpProxyView`): the editor runs its MCP server on a loopback port of its own (`--mcp=127.0.0.1:port`, a token per run in `XKNX_MCP_AUTH_TOKEN`). MCP clients reach it at `/api/xknx_editor/mcp`, signed in with a Home Assistant access token of an administrator (`requires_auth`); the proxy forwards with the editor's token instead. The process also passes `--home-assistant=integration` (no update check and no File > Exit in the editor) and `--mcp-url` (the address the editor shows, from Home Assistant's own URL).
- **Manager** (`manager.py`): installs, starts and watches the editor, starts it again with growing waits when it ends; its status is what the proxy shows while the editor is not running.

## Commands

```bash
uv venv --python 3.14 && uv pip install -r requirements_test.txt   # Test environment
.venv/bin/pytest           # Tests (Linux or macOS: Home Assistant's test fixtures need fcntl)
uvx ruff check             # Lint
uvx ruff format            # Format
```

The tests use Home Assistant's own test fixtures (`pytest-home-assistant`, imported as `pytest_homeassistant`), which come with the Home Assistant release they belong to; install the one of the release to test against.

Changes to the environment, the process, the proxy or the panel are also checked in a real Home Assistant (its own container image, so Alpine and its Python): see `scripts/dev/README.md` (Docker, or WSL on Windows; onboarding, panel screenshots, tests and hassfest against a dev or beta build).

## Conventions

- Follow Home Assistant's integration rules (config flow, `runtime_data`, translations in `translations/` with a complete `en.json`; custom integrations have no `strings.json`).
- The release tag must equal `version` in `manifest.json`. `hacs.json` names the minimum Home Assistant version.
- Never add `requirements` to the manifest: the editor's packages belong in its own environment.
- Every change in behaviour comes with tests in `tests/`.
