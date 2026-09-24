"""Filesystem locations shared across the backend.

Everything that used to be resolved against the process working directory (or
against a module's own folder, which changes when a module is moved) is anchored
here instead, so the app behaves the same wherever it is started from.
"""
from pathlib import Path

# Backend/ — the folder that holds main.py, .env, scripts/ and temp/.
BASE_DIR = Path(__file__).resolve().parent.parent

ENV_FILE = BASE_DIR / ".env"

# Original voice/batch calling app assets.
OUTBOUND_DIR = BASE_DIR / "outbound"
TEMPLATES_DIR = OUTBOUND_DIR / "templates"
STATIC_DIR = OUTBOUND_DIR / "static"
SYSTEM_PROMPT_FILE = OUTBOUND_DIR / "system_prompt.txt"

# Runtime data. `scripts/` holds the XML agent scripts and is a docker volume, so
# its location must not change; `temp/` is scratch space for batch CSV work.
SCRIPTS_DIR = BASE_DIR / "scripts"
TEMP_DIR = BASE_DIR / "temp"
