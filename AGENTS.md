# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository. `CLAUDE.md` only refers to this file, so keep all instructions here.

## What this is

A Home Assistant custom integration (domain `xknx_editor`) that runs the [XKNX Editor](https://github.com/XKNX/xknx-editor) in its browser mode and shows it in Home Assistant's sidebar. It is published through the Home Assistant Marketplace (HACS), which needs one integration per repository in `custom_components/<domain>/`.

## Architecture

- **Two parts.** The integration itself runs inside Home Assistant and has no `requirements`: it only uses what Home Assistant ships (aiohttp, voluptuous, ...). The editor runs as a process of its own (`process.py`), with its own frame loop, threads and packages.
- **The editor's environment** (`environment.py`): a virtual environment with Home Assistant's Python, created and filled with uv (`python -m uv`, which Home Assistant installs its own packages with) from a locked requirements file of the editor release `const.EDITOR_VERSION`, attached to the integration's own release (`const.REQUIREMENTS_URL`) with the wheels it names by `scripts/release_files.py` (`.github/workflows/release.yml`), so the editor's releases keep only what its users download. So the editor gets exactly the package versions it was released with, and Home Assistant's packages stay untouched. `--no-build`: every package must come as a wheel (Home Assistant's image is Alpine Linux, so musllinux wheels for CPython of Home Assistant's version, x86_64 and aarch64). The environment is created again when Home Assistant's Python version changes.
- **.NET** (`dotnet.py`): the editor reads keys from the KNX authoring tool's assemblies with helpers of its own on a .NET runtime. The integration installs Microsoft's runtime archive for this platform (`const.DOTNET_VERSION`, by SHA-512 in `const.DOTNET_RUNTIMES`) and the editor's helpers (`keyextract.zip` next to the requirements file, whose SHA-256 the requirements file names), and passes `DOTNET_ROOT`, `PATH` and `XKNX_KEYEXTRACT_DIR` to the editor. A failure here only logs a warning: the editor runs without those extractions.
- **Folders:** the environment, downloads and the editor's cache go to `/config/.cache/xknx_editor` (Home Assistant leaves `.cache` out of its backups); the editor's settings, catalog and file folder go to `/config/xknx_editor` (kept in backups).
- **Access** (`proxy.py`, `frontend/panel.js`): like the Ingress of Home Assistant apps. The panel (administrators only, unless the option `allow_non_admins`, "Editor for all users", opens panel, sessions and MCP to every signed-in user) gets a session over Home Assistant's websocket (`xknx_editor/session`, renewed with `xknx_editor/session/validate`) and stores it in the cookie `xknx_editor_session` for `/api/xknx_editor/`. One session per browser tab (kept in `sessionStorage` while it is live): Home Assistant may create the panel more than once, and the editor gives control back only to a returning session. The proxy view at `/api/xknx_editor/ui/` forwards HTTP and websocket requests with a live session to the editor on `127.0.0.1`. The editor requires a password (random, in the config entry); only the proxy signs in with it, once per panel session and editor run, and sends the editor's session cookie instead of the browser's cookies.
- **MCP** (`proxy.McpProxyView`): the editor runs its MCP server on a loopback port of its own (`--mcp=127.0.0.1:port`, a token per run in `XKNX_MCP_AUTH_TOKEN`). MCP clients reach it at `/api/xknx_editor/mcp`, signed in with a Home Assistant access token of an administrator, or of every user with `allow_non_admins` (`requires_auth`); the proxy forwards with the editor's token instead. The process also passes `--home-assistant` (a new version is only announced: Home Assistant updates the editor with the integration) and `--mcp-url` (the address the editor shows, from Home Assistant's own URL).
- **Manager** (`manager.py`): installs, starts and watches the editor, starts it again with growing waits when it ends; its status is what the proxy shows while the editor is not running.

## Commands

```bash
uv venv --python 3.14 && uv pip install -r requirements_test.txt   # Test environment
.venv/bin/pytest           # Tests (Linux or macOS: Home Assistant's test fixtures need fcntl)
.venv/bin/pytest --cov     # Tests with coverage (CI uploads it to Codecov, PRs are not checked yet)
uvx ruff check             # Lint
uvx ruff format            # Format
```

The tests use Home Assistant's own test fixtures (`pytest-home-assistant`, imported as `pytest_homeassistant`), which come with the Home Assistant release they belong to; install the one of the release to test against.

Changes to the environment, the process, the proxy or the panel are also checked in a real Home Assistant (its own container image, so Alpine and its Python): see `scripts/dev/README.md` (Docker, or WSL on Windows; onboarding, panel screenshots, tests and hassfest against a dev or beta build).

## Conventions

- Follow Home Assistant's integration rules (config flow, `runtime_data`, translations in `translations/` with a complete `en.json`; custom integrations have no `strings.json`).
- The release tag must equal `version` in `manifest.json`. `hacs.json` names the minimum Home Assistant version.
- A new editor release is followed automatically (`.github/workflows/editor-release.yml`, every 10 minutes or run manually): `scripts/editor_release.py` sets `const.EDITOR_VERSION` and the manifest version on a branch `release/<version>`, the tests and the release checks run on it and build the editor's files, then `main` moves to that commit and the release is published with its files and the editor's release notes. A failed run leaves the branch; the scheduled checks skip that version until a manual run. Integration-only releases are made by hand: publishing a release runs `release.yml`, which attaches the files.
- **Versions follow the editor:** the integration's version (`manifest.json`, release tag without a `v`) is the editor version it installs, e.g. `0.9.0` installs editor 0.9.0. A fix of the integration alone before the next editor release adds a fourth number: `0.9.0.1`, `0.9.0.2`. Home Assistant sorts these after `0.9.0` and before `0.9.1`; never use `0.9.0-1` or `0.9.0.post1`, which it sorts before `0.9.0`, so no update would be offered.
- Never add `requirements` to the manifest: the editor's packages belong in its own environment.
- Every change in behaviour comes with tests in `tests/`.
