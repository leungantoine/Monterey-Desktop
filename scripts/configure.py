"""Save tool trust for this explicitly installed local helper, preserving other settings."""
from pathlib import Path
import os
import re
import tomllib

config = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex'))) / 'config.toml'
text = config.read_text() if config.exists() else ''
header = '[plugins."monterey-desktop@personal-local".mcp_servers.monterey-desktop]'
pattern = re.compile(r'^'+re.escape(header)+r'\s*\n(.*?)(?=^\[|\Z)', re.M | re.S)
match = pattern.search(text)
if match:
    body = match.group(1)
    setting = re.compile(r'^default_tools_approval_mode\s*=.*$', re.M)
    if setting.search(body):
        body = setting.sub('default_tools_approval_mode = "approve"', body)
    else:
        body = body.rstrip()+'\ndefault_tools_approval_mode = "approve"\n\n'
    updated = text[:match.start()]+header+'\n'+body+text[match.end():]
else:
    updated = text.rstrip()+'\n\n'+header+'\ndefault_tools_approval_mode = "approve"\n'
# Validate before writing; retain a rollback copy once.
tomllib.loads(updated)
if updated != text:
    backup = config.with_name('config.toml.before-monterey-tool-policy')
    if not backup.exists() and config.exists():
        backup.write_text(text)
        backup.chmod(config.stat().st_mode & 0o777)
    temporary = config.with_name('config.toml.monterey-tmp')
    temporary.write_text(updated)
    temporary.chmod(config.stat().st_mode & 0o777 if config.exists() else 0o600)
    temporary.replace(config)
print('Configured saved tool approvals for the local Monterey Desktop plugin.')
