#!/bin/sh
# A test environment in /root/tests on top of the Home Assistant of the image (WSL, see
# README.md): Home Assistant's pytest-home-assistant and hassfest packages, built from a
# checkout of its core repository (/root/core, the branch the image was built from) as long as
# they are not on PyPI for that release.
set -e
unset UV_SYSTEM_PYTHON
export UV_EXTRA_INDEX_URL=https://wheels.home-assistant.io/musllinux-index/
if [ ! -d /root/core ]; then
  apk add --no-cache git >/dev/null
  git clone -q --depth 1 --branch "${1:-dev}" https://github.com/home-assistant/core.git /root/core
fi
python3 -m uv venv --clear --system-site-packages --python /usr/local/bin/python3 /root/tests >/dev/null
# Without their dependencies, which would bring Home Assistant from PyPI; those besides
# homeassistant follow (pytest-home-assistant's list, and ruff for hassfest).
python3 -m uv pip install --python /root/tests/bin/python --no-deps \
  /root/core/packages/pytest-home-assistant /root/core/packages/hassfest
deps=$(python3 - <<'PY'
import tomllib
project = tomllib.load(open("/root/core/packages/pytest-home-assistant/pyproject.toml", "rb"))
print(" ".join(d for d in project["project"]["dependencies"] if d != "homeassistant"))
PY
)
# shellcheck disable=SC2086
python3 -m uv pip install --python /root/tests/bin/python --index-strategy unsafe-best-match \
  $deps ruff
/root/tests/bin/python -c "import homeassistant.const as c; print('Home Assistant', c.__version__)"
