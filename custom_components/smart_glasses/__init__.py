"""Smart Glasses — HA panel + endpoints for a 600x600 glasses Web App.

Setup order on each HA start:
  1. Load persisted store (entities + pairings).
  2. Register the HTTP views (pairing, entities, glasses HTML).
  3. Register the management panel at /smart-glasses (loads frontend/panel.js).
"""

from __future__ import annotations

import logging

from homeassistant.components.frontend import async_register_built_in_panel
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    PANEL_JS_ROUTE,
    PANEL_URL,
    STORAGE_KEY,
    STORAGE_VERSION,
    VERSION,
)
from .store import SmartGlassesStore
from .views import ALL_VIEWS

_LOGGER = logging.getLogger(__name__)

# URL HA's frontend fetches to load the panel custom element. The `?v=`
# bust forces browsers (and CDNs that key cache by full URL) to re-fetch
# on each version bump.
_PANEL_JS_URL = f"{PANEL_JS_ROUTE}?v={VERSION}"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    store = SmartGlassesStore(hass)
    await store.async_load()
    hass.data.setdefault(DOMAIN, {})["store"] = store

    # All file serving (panel.js, glasses.html) goes through HomeAssistantView
    # rather than StaticPathConfig so we control the Cache-Control headers.
    # CDNs like Cloudflare cache JS/HTML aggressively otherwise.
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
        require_admin=True,
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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Called by HA when the integration is *removed* (not just unloaded).
    Wipes our persisted storage so a fresh install starts from zero — no
    orphaned pairings, no stale audit log, no leftover card config.

    HA itself doesn't prompt for confirmation here; the user already
    confirmed in the Devices & Services UI before this is invoked.
    """
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    try:
        await store.async_remove()
    except Exception:  # noqa: BLE001
        _LOGGER.exception("could not remove smart_glasses storage on uninstall")
