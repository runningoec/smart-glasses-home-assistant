"""Smart Glasses — HA panel + endpoints for a 600x600 glasses Web App.

Setup order on each HA start:
  1. Load persisted store (entities + pairings).
  2. Register the HTTP views (pairing, entities, glasses HTML).
  3. Register the management panel at /smart-glasses (loads frontend/panel.js).
"""

from __future__ import annotations

import logging

from homeassistant.components.frontend import async_register_built_in_panel
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    FRONTEND_DIR,
    PANEL_JS_PATH,
    PANEL_URL,
)
from .store import SmartGlassesStore
from .views import ALL_VIEWS

_LOGGER = logging.getLogger(__name__)

# URL where the panel's JS bundle is served. Has to match what
# async_register_built_in_panel(js_url=...) points at.
_PANEL_JS_URL = "/smart_glasses_static/panel.js"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    store = SmartGlassesStore(hass)
    await store.async_load()
    hass.data.setdefault(DOMAIN, {})["store"] = store

    # Serve the frontend bundle as a static path so HA's frontend can fetch
    # /smart_glasses_static/panel.js, and so anything else in frontend/ (e.g.
    # future CSS or icons) is reachable.
    await hass.http.async_register_static_paths([
        StaticPathConfig(
            url_path="/smart_glasses_static",
            path=str(FRONTEND_DIR),
            cache_headers=False,
        ),
    ])

    for view_cls in ALL_VIEWS:
        hass.http.register_view(view_cls())

    # The panel itself — HA's frontend renders <smart-glasses-panel> when the
    # user navigates to /smart-glasses, loading panel.js as a custom element.
    async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title="Smart Glasses",
        sidebar_icon="mdi:glasses",
        frontend_url_path=PANEL_URL.lstrip("/"),
        config={
            "_panel_custom": {
                "name": "smart-glasses-panel",
                "embed_iframe": False,
                "trust_external": False,
                "js_url": _PANEL_JS_URL,
            },
        },
        require_admin=False,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down the panel. Views are not individually unregisterable in HA,
    but they'll be GC'd when the process restarts. For a singleton integration
    that's acceptable."""
    from homeassistant.components.frontend import async_remove_panel

    async_remove_panel(hass, PANEL_URL.lstrip("/"))
    hass.data.pop(DOMAIN, None)
    return True
