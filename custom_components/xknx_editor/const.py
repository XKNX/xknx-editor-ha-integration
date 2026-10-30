"""Constants of the XKNX Editor integration."""

from typing import Final

DOMAIN: Final = "xknx_editor"

# The editor release this version of the integration installs.
EDITOR_VERSION: Final = "0.9.2"
# The locked requirements of that editor release (third-party packages from PyPI with hashes, the
# editor's own packages as wheels), attached to the integration's own release ({release}, the
# manifest's version) by scripts/release_files.py, so the editor's releases keep only what its
# users download.
REQUIREMENTS_URL: Final = (
    "https://github.com/XKNX/xknx-editor-ha-integration/releases/download/"
    "{release}/xknx-editor-{version}-requirements.txt"
)

# The .NET runtime the editor reads the KNX authoring tool's assemblies with (signing key, project
# log key, MyKNX sign-in): the archives Microsoft publishes, by platform, with their SHA-512.
DOTNET_VERSION: Final = "10.0.14"
_DOTNET_URL: Final = (
    "https://builds.dotnet.microsoft.com/dotnet/Runtime/"
    "{version}/dotnet-runtime-{version}-{rid}.tar.gz"
)
DOTNET_RUNTIMES: Final = {
    rid: (_DOTNET_URL.format(version=DOTNET_VERSION, rid=rid), sha512)
    for rid, sha512 in (
        (
            "linux-musl-x64",
            "b6e1855b3ece331d6d5b1daf4ed0a209931b8527ece85eff6d843ff3e6bccd4bc8b0c484cada7832a453cf"
            "9130e8fe83df961f129fcf4e61199ad701814039f0",
        ),
        (
            "linux-musl-arm64",
            "08f19dd39dfe705e1333c3ee63c37be027e0627768f5048be6be7767c239aca0f83a0ed34ac0445cd2f63bbf"
            "5edd9483a6ac184e263923284f294e06b0f6692a",
        ),
        (
            "linux-x64",
            "634326028645f38731c8c4e71a68356e4ca0d6b6607139a234ab760687f742063edaf33a1f20dda615742d2d"
            "c8300ca2ff857619d51fbb38090383b71334af89",
        ),
        (
            "linux-arm64",
            "99e1d6eb5d3de935853404fd378c77cbf813bd3ae094837b5a589000a492c289bf0d01d8a6bff99b4470572d"
            "9f85dc0b41239b5b7801e031197b83d527cdfeb2",
        ),
    )
}
# The editor's key-extraction helpers for that runtime: next to the requirements file (its name
# with "keyextract.zip" in place of "requirements.txt"), which names their SHA-256.
HELPERS_NAME: Final = "keyextract.zip"

# Config entry data: the password the editor process accepts (only the proxy signs in with it).
CONF_EDITOR_PASSWORD: Final = "editor_password"
# Option: every signed-in user gets the panel, not only administrators.
CONF_ALLOW_NON_ADMINS: Final = "allow_non_admins"
# Option: another requirements file (a URL or a path on the Home Assistant host), e.g. to try an
# editor build before its release.
CONF_REQUIREMENTS: Final = "requirements"

# Folders below the Home Assistant configuration folder. The environment and downloads go to
# ".cache", which Home Assistant leaves out of its backups (they are installed again when
# missing); the editor's settings, catalog and files are kept in the backups.
ENVIRONMENT_FOLDER: Final = ".cache/xknx_editor"
DATA_FOLDER: Final = "xknx_editor"

URL_BASE: Final = "/api/xknx_editor"
UI_URL: Final = f"{URL_BASE}/ui/"
# The editor's MCP server, for MCP clients signed in with a Home Assistant access token.
MCP_URL: Final = f"{URL_BASE}/mcp"
STATIC_URL: Final = "/xknx_editor_static"
PANEL_URL_PATH: Final = "xknx-editor"
PANEL_TITLE: Final = "XKNX Editor"
PANEL_ICON: Final = "mdi:chip"
PANEL_ELEMENT: Final = "xknx-editor-panel"

# The cookie the panel sets for the proxy, valid below URL_BASE only.
SESSION_COOKIE: Final = "xknx_editor_session"
# A session the panel stops renewing (the panel was closed) ends after this time.
SESSION_TIMEOUT: Final = 15 * 60
# The editor's own session cookie (see the editor's web server).
EDITOR_COOKIE: Final = "xknx_web_session"
