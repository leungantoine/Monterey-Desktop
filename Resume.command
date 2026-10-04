#!/bin/sh
set -eu
companion_data="${MONTEREY_DESKTOP_DATA_DIR:-$HOME/.local/share/monterey-desktop}"
mkdir -p "$companion_data"
rm -f "$companion_data/.paused"
