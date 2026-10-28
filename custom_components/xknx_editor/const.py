"""Constants of the XKNX Editor integration."""

from typing import Final

DOMAIN: Final = "xknx_editor"

# The editor release this version of the integration installs.
EDITOR_VERSION: Final = "0.8.0"
# The locked requirements of an editor release (third-party packages from PyPI with hashes, the
# editor's own packages as wheels attached to the release).
REQUIREMENTS_URL: Final = (
    "https://github.com/XKNX/xknx-editor/releases/download/"
    "xknx-editor-v{version}/xknx-editor-{version}-requirements.txt"
)

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
