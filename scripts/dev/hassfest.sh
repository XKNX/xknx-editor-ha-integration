#!/bin/sh
# hassfest, Home Assistant's integration validator, from the core checkout of setup_tests.sh on
# this repository's integration (a copy: hassfest wants a folder named like the domain).
set -e
REPO=$(cd "$(dirname "$0")/../.." && pwd)
rm -rf /root/hassfest-check && mkdir -p /root/hassfest-check
cp -r "$REPO/custom_components/xknx_editor" /root/hassfest-check/
cd /root/core
exec /root/tests/bin/python -m script.hassfest --action validate \
  --integration-path /root/hassfest-check/xknx_editor
