#!/bin/sh
set -eu
companion_data="${MONTEREY_DESKTOP_DATA_DIR:-$HOME/.local/share/monterey-desktop}"
mkdir -p "$companion_data"
touch "$companion_data/.paused"
