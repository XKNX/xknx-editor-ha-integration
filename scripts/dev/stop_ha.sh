#!/bin/sh
# Stop the Home Assistant of run_ha.sh (and with it the editor it started).
pkill -TERM -f "python3 -m homeassistant" || exit 0
i=0
while pgrep -f "python3 -m homeassistant" >/dev/null && [ $i -lt 60 ]; do
  sleep 1
  i=$((i + 1))
done
pgrep -fl "editor_gui|homeassistant" || echo "stopped"
