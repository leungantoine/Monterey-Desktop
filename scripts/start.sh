#!/bin/sh
set -eu
companion_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
companion_data="${MONTEREY_DESKTOP_DATA_DIR:-$HOME/.local/share/monterey-desktop}"
companion_python="$companion_data/.venv/bin/python"
if [ ! -x "$companion_python" ]; then
    echo "Monterey Desktop dependencies are missing. Run $companion_root/install.command locally." >&2
    exit 1
fi
export MONTEREY_DESKTOP_DATA_DIR="$companion_data"
export PYTHONDONTWRITEBYTECODE=1
exec "$companion_python" "$companion_root/desktop.py" serve
