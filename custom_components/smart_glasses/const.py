"""Constants for the Smart Glasses integration."""

from __future__ import annotations

from pathlib import Path

DOMAIN = "smart_glasses"

# URLs registered with HA's HTTP layer.
PANEL_URL = "/smart-glasses"  # Custom panel (logged-in users in a browser).
GLASSES_APP_URL = "/smart-glasses-app"  # Static page served to the glasses.
API_PREFIX = "/api/smart_glasses"

# How HA Store keys the JSON file under config/.storage/.
STORAGE_VERSION = 1
STORAGE_KEY = "smart_glasses"

# Bound on how many entities the user can glance at once. Tight cap because the
# glasses are 600x600 and even an 8-cell grid is dense.
MAX_ENTITIES = 8

# Length of the human-readable pairing code shown on the glasses.
PAIRING_CODE_LENGTH = 6

# Window during which an unapproved pairing remains usable, before the glasses
# must request a new code.
PAIRING_TTL_SECONDS = 300

# Resolves frontend bundle paths relative to this file (no matter where HA
# installs the integration on disk).
FRONTEND_DIR: Path = Path(__file__).parent / "frontend"
PANEL_JS_PATH: Path = FRONTEND_DIR / "panel.js"
GLASSES_HTML_PATH: Path = FRONTEND_DIR / "glasses.html"
