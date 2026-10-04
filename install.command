#!/bin/zsh
set -e
companion_root="${0:A:h}"
companion_data="${MONTEREY_DESKTOP_DATA_DIR:-$HOME/.local/share/monterey-desktop}"
mkdir -p "$companion_data"
if [[ ! -x "$companion_data/.venv/bin/python" ]]; then
  companion_python="$(command -v python3 || true)"
  if [[ -z "$companion_python" ]]; then
    print -u2 'Python 3.12 is required. Install it before running this installer.'
    exit 1
  fi
  "$companion_python" -m venv "$companion_data/.venv"
fi
"$companion_data/.venv/bin/python" -m pip install --only-binary=:all: -r "$companion_root/requirements.txt"
companion_codex="$(command -v codex || true)"
if [[ -z "$companion_codex" && -x "$HOME/.local/bin/codex" ]]; then
  companion_codex="$HOME/.local/bin/codex"
fi
if [[ -z "$companion_codex" ]]; then
  print -u2 'Codex was not found. Install it or add it to PATH, then rerun this installer.'
  exit 1
fi
if [[ ! -f "$HOME/.agents/plugins/marketplace.json" || ! -f "$HOME/.codex/plugins/monterey-desktop/.codex-plugin/plugin.json" ]]; then
  print -u2 'Keep the source in ~/.codex/plugins/monterey-desktop and its personal-local catalog entry in ~/.agents/plugins/marketplace.json. See README.md.'
  exit 1
fi
"$companion_codex" plugin marketplace add "$HOME"
"$companion_codex" plugin add monterey-desktop@personal-local
"$companion_data/.venv/bin/python" "$companion_root/scripts/configure.py"
companion_config="${CODEX_HOME:-$HOME/.codex}/config.toml"
if "$companion_data/.venv/bin/python" - "$companion_config" <<'PY'
import sys, tomllib
from pathlib import Path
config = Path(sys.argv[1])
data = tomllib.loads(config.read_text()) if config.exists() else {}
sys.exit(0 if 'monterey-desktop' in data.get('mcp_servers', {}) else 1)
PY
then
  "$companion_codex" mcp remove monterey-desktop
fi
print 'Installed Monterey Desktop plugin. Start a new Codex session to load its tools and skill.'
