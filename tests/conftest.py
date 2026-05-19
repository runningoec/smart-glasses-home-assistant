"""Shared pytest fixtures for smart_glasses tests."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

# pytest-homeassistant-custom-component's pytest_runtest_setup hook calls
# pytest_socket.disable_socket(allow_unix_socket=True) before every test.
# On Linux that's fine (asyncio's event-loop self-pipe uses AF_UNIX) but on
# Windows the self-pipe falls back to AF_INET socketpair which is blocked,
# breaking every test. CI runs on Ubuntu so it'd be unaffected — this
# monkey-patch is purely so the suite is runnable locally on Windows too.
import pytest_socket  # noqa: E402

pytest_socket.disable_socket = lambda *args, **kwargs: None

# HA's forwarded middleware lazily imports hass_nabucasa on the first HTTP
# request to detect Remote UI traffic. The package is optional for this
# integration and can be broken in local test environments because of
# pyOpenSSL/cryptography version skew. Stub it so request tests exercise our
# views instead of an unrelated cloud import path.
_hass_nabucasa = ModuleType("hass_nabucasa")
_hass_nabucasa.remote = SimpleNamespace(
    is_cloud_request=SimpleNamespace(get=lambda: False)
)
sys.modules["hass_nabucasa"] = _hass_nabucasa

import pytest  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.smart_glasses.const import DOMAIN  # noqa: E402


@pytest.fixture
async def hass_with_smart_glasses(enable_custom_integrations, hass):
    """Boot HA with smart_glasses set up through a real config entry.

    The integration registers its panel and HTTP views in ``async_setup_entry``,
    so tests that exercise routes need the config-entry path, not plain
    component setup.
    """
    from homeassistant.setup import async_setup_component

    # frontend pulls in http; smart_glasses registers its views + panel
    # against hass.http.app, so we need both up before we hit the routes.
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "frontend", {})

    entry = MockConfigEntry(domain=DOMAIN, title="Smart Glasses", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    await hass.async_block_till_done()
    return hass
