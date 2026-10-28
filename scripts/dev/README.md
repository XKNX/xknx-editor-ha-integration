# Testing with a Real Home Assistant

These scripts run the integration in Home Assistant's own container image: Alpine Linux with Home Assistant's Python, like on users' systems. Local files (tokens, screenshots, downloads) go to `.dev/`, which Git ignores.

## Home Assistant

**Linux or macOS with Docker:** run the image with this repository's integration mounted:

```bash
docker run -d --name ha-dev --network host \
  -v "$PWD/.dev/config:/config" \
  -v "$PWD/custom_components/xknx_editor:/config/custom_components/xknx_editor" \
  ghcr.io/home-assistant/home-assistant:dev
```

**Windows without Docker:** import the image as a WSL distribution and run Home Assistant in it (port 8123 is reachable from Windows as `localhost:8123`):

```bash
uv run --python 3.14 scripts/dev/oci_rootfs.py ghcr.io home-assistant/home-assistant dev \
  .dev/rootfs.tar .dev/layers
wsl --import HomeAssistantDev .dev/distro .dev/rootfs.tar --version 2
wsl -d HomeAssistantDev -u root -- sh scripts/dev/run_ha.sh
```

Use the tag of the release to test against (`dev`, `beta`, or a version such as `2026.11.0`).

## Setting Up the Integration

```bash
python scripts/dev/setup_ha.py
```

This onboards Home Assistant (user `dev`, password `dev-password`) and adds XKNX Editor. To try an editor build before its release, build its wheels and requirements file with `editor_requirements.py` and pass the file's path on the Home Assistant host as the argument.

## Checks

- **Panel:** `uv run --with playwright scripts/dev/check_panel.py .dev/shots` takes screenshots of the panel at desktop and phone size.
- **Tests and hassfest** on the image's Home Assistant (WSL): `setup_tests.sh` once, then `run_tests.sh` and `hassfest.sh`:

  ```bash
  wsl -d HomeAssistantDev -u root -- sh scripts/dev/setup_tests.sh
  wsl -d HomeAssistantDev -u root -- sh scripts/dev/run_tests.sh
  wsl -d HomeAssistantDev -u root -- sh scripts/dev/hassfest.sh
  ```
