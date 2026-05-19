"""Shared pytest fixtures for smart_glasses tests."""

from __future__ import annotations

# pytest-homeassistant-custom-component's pytest_runtest_setup hook calls
# pytest_socket.disable_socket(allow_unix_socket=True) before every test.
# On Linux that's fine (asyncio's event-loop self-pipe uses AF_UNIX) but on
# Windows the self-pipe falls back to AF_INET socketpair which is blocked,
# breaking every test. CI runs on Ubuntu so it'd be unaffected — this
# monkey-patch is purely so the suite is runnable locally on Windows too.
import pytest_socket  # noqa: E402

pytest_socket.disable_socket = lambda *args, **kwargs: None

import pytest  # noqa: E402


@pytest.fixture
async def hass_with_smart_glasses(enable_custom_integrations, hass):
    """Boot HA with smart_glasses + its http dependency loaded. Yields the
    hass instance; tests reach the integration's views through
    ``hass_client`` / ``hass_client_no_auth`` (which need ``hass.http.app``).
    """
    from homeassistant.setup import async_setup_component
    # frontend pulls in http; smart_glasses registers its views + panel
    # against hass.http.app, so we need both up before we hit the routes.
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "frontend", {})
    assert await async_setup_component(hass, "smart_glasses", {})
    await hass.async_block_till_done()
    return hass
