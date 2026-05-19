"""Config flow for the Smart Glasses integration.

Single-step flow. There's nothing to configure at install time — the
integration is fully managed from its panel once added.
"""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries

from .const import DOMAIN


class SmartGlassesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the one-step add flow for Smart Glasses."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        # Singleton: only allow one entry. Pairings + entity selection live on
        # this single entry's storage; multiple entries would just be confusing.
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="Smart Glasses", data={})

        return self.async_show_form(step_id="user")
