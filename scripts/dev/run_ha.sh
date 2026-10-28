#!/bin/sh
# Run the Home Assistant of the image's root file system (WSL, see README.md) like its container
# does, with the configuration folder /root/ha-config and this repository's integration linked
# in. Logs: /root/ha-config/home-assistant.log.
REPO=$(cd "$(dirname "$0")/../.." && pwd)
CONFIG=/root/ha-config
mkdir -p "$CONFIG/custom_components"
ln -sfn "$REPO/custom_components/xknx_editor" "$CONFIG/custom_components/xknx_editor"
[ -f "$CONFIG/configuration.yaml" ] || cat > "$CONFIG/configuration.yaml" <<'YAML'
default_config:
logger:
  default: warning
  logs:
    custom_components.xknx_editor: debug
YAML
# The image's environment (its Dockerfile).
export LANG=C.UTF-8
export UV_SYSTEM_PYTHON=true UV_NO_CACHE=true
export UV_EXTRA_INDEX_URL=https://wheels.home-assistant.io/musllinux-index/
export PIP_EXTRA_INDEX_URL=https://wheels.home-assistant.io/musllinux-index/
cd "$CONFIG"
exec python3 -m homeassistant --config "$CONFIG" >> "$CONFIG/console.log" 2>&1
