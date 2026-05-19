"""Constants for the Smart Glasses integration."""

from __future__ import annotations

import json
from pathlib import Path

DOMAIN = "smart_glasses"

# Read the integration version from manifest.json so we don't have to track it
# in two places. Used as a cache-buster on the panel.js URL — every release
# bumps the version, which forces fresh fetches through any CDN sitting in
# front of the user's HA (e.g. Cloudflare Tunnel).
try:
    with open(Path(__file__).parent / "manifest.json", encoding="utf-8") as _mf:
        VERSION = json.load(_mf).get("version", "0")
except Exception:  # noqa: BLE001
    VERSION = "0"

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

# Hard upper bound on how long an unapproved pairing can live, regardless of
# polling activity. Backstop in case a polling client misbehaves.
PAIRING_TTL_SECONDS = 300

# Time without a token-poll after which a pending pairing is considered
# abandoned and gets auto-pruned from the list. The glasses Web App polls
# every POLL_PAIR_MS (2s) so anything past ~30s means the tab/glasses
# closed and the pairing is dead from the user's POV.
PAIRING_INACTIVE_SECONDS = 30

# Cap on unapproved pairing sessions across the whole install. A bad actor
# hitting /pair/start in a loop can't blow up storage past this.
MAX_PENDING_PAIRINGS = 50

# Per-IP rate limit on /pair/start (token bucket). Refilled at one token per
# second; this is also the burst allowance.
PAIR_START_PER_IP_PER_MIN = 6

# Resolves frontend bundle paths relative to this file (no matter where HA
# installs the integration on disk).
FRONTEND_DIR: Path = Path(__file__).parent / "frontend"
PANEL_JS_PATH: Path = FRONTEND_DIR / "panel.js"
GLASSES_HTML_PATH: Path = FRONTEND_DIR / "glasses.html"
FAVICON_PATH: Path = FRONTEND_DIR / "favicon-192x192.png"

# Route that serves panel.js. Lives under /api/ so it goes through a
# HomeAssistantView (which lets us send explicit Cache-Control: no-store)
# rather than HA's StaticPathConfig (which doesn't expose per-response
# header control and lets CDNs apply their default Edge Cache TTL).
PANEL_JS_ROUTE = "/api/smart_glasses/panel.js"
