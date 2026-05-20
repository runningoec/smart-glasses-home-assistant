"""Admin-only protection for the panel-facing API routes."""

from __future__ import annotations

import pytest
from homeassistant.auth.const import GROUP_ID_USER


@pytest.fixture
def hass_client_non_admin(hass, aiohttp_client, hass_access_token, socket_enabled):
    """Return an authenticated HTTP client for a non-admin HA user."""

    async def auth_client():
        user = await hass.auth.async_create_user("limited-user", group_ids=[GROUP_ID_USER])
        refresh_token = await hass.auth.async_create_refresh_token(
            user,
            client_id="https://smart-glasses.test/client",
        )
        access_token = hass.auth.async_create_access_token(refresh_token)
        return await aiohttp_client(
            hass.http.app,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    return auth_client


@pytest.mark.asyncio
async def test_non_admin_cannot_access_panel_endpoints(
    hass_with_smart_glasses,
    hass,
    hass_client_no_auth,
    hass_client_non_admin,
):
    """The panel UI is admin-only, and the backing API must match that."""
    hass.states.async_set("light.kitchen", "off")

    glasses = await hass_client_no_auth()
    start = await (await glasses.post("/api/smart_glasses/pair/start")).json()
    sid = start["session_id"]
    code = start["code"]

    client = await hass_client_non_admin()

    pairings = await client.get("/api/smart_glasses/pairings")
    assert pairings.status == 403

    approve = await client.post(
        "/api/smart_glasses/pair/approve",
        json={"code": code, "session_id": sid},
    )
    assert approve.status == 403

    revoke = await client.delete(f"/api/smart_glasses/pair/{sid}")
    assert revoke.status == 403

    cards_get = await client.get("/api/smart_glasses/cards")
    assert cards_get.status == 403

    cards_put = await client.put(
        "/api/smart_glasses/cards",
        json={
            "cards": [{
                "id": "home",
                "name": "Home",
                "items": [{"type": "entity", "entity_id": "light.kitchen"}],
            }]
        },
    )
    assert cards_put.status == 403

    yaml_get = await client.get("/api/smart_glasses/cards/yaml")
    assert yaml_get.status == 403

    yaml_put = await client.put(
        "/api/smart_glasses/cards/yaml",
        data="cards: []\n",
        headers={
            "Authorization": client.session.headers["Authorization"],
            "Content-Type": "text/yaml",
        },
    )
    assert yaml_put.status == 403

    audit = await client.get("/api/smart_glasses/audit")
    assert audit.status == 403
