# XKNX Editor for Home Assistant

This integration runs [XKNX Editor](https://github.com/XKNX/xknx-editor), the KNX project editor, on your Home Assistant server and shows it in the sidebar. It works with Home Assistant OS and with Home Assistant Container, which can't install apps.

![XKNX Editor in Home Assistant](images/editor.png)

![XKNX Editor in Home Assistant, light theme](images/editor-light.png)

## Installation

1. Open **Settings** > **Marketplace** in Home Assistant, search for **XKNX Editor** and install it.
2. Restart Home Assistant.
3. Add the integration under **Settings** > **Devices & services** > **Add integration** > **XKNX Editor**.

On its first start, the integration installs the editor into an environment of its own. That takes a few minutes and needs internet access. Until it's done, the **XKNX Editor** panel in the sidebar shows the progress.

## How It Works

- **A process of its own.** The editor runs next to Home Assistant, in its own Python environment, with exactly the package versions it was released with. Home Assistant's own packages aren't changed. The environment uses Home Assistant's Python and is installed again when Home Assistant moves to another Python version.
- **Administrators only.** The panel is available to Home Assistant administrators, and the editor is reachable only through Home Assistant's sign-in.
- **Your files.** The editor's settings, product catalog and file folder are in `xknx_editor` in your configuration folder and are part of Home Assistant's backups. Its environment is in `.cache/xknx_editor`, which backups leave out; it's installed again when it's missing.

## Connecting to the KNX Bus

The editor connects to your KNX/IP interface or router itself, next to Home Assistant's KNX integration, so the interface needs a free tunnel. Searching for interfaces and KNX/IP routing (multicast) need Home Assistant's network: with Home Assistant Container, run it with `network_mode: host`.

## Options

**Requirements file** (under the integration's **Configure**): leave it empty. It only exists to try an editor build before its release, as a URL or a path on the Home Assistant host.

## License

Apache License 2.0. XKNX Editor itself is licensed under the GNU General Public License v2.0 and runs as a separate program.
