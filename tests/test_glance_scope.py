"""HA-integration test for the scope-limited glance API.

The glasses-side endpoints expose only the entities + actions on cards.
These tests prove that the proxy rejects attempts to read or fire
anything outside that scope, even with a valid token.
"""

from __future__ import annotations

import pytest
from homeassistant.core import ServiceCall, callback


async def _pair_and_get_token(hass_client_no_auth, hass_client) -> str:
    """Helper: complete the pair/approve handshake and return the token."""
    glasses = await hass_client_no_auth()
    admin = await hass_client()

    start = await (await glasses.post("/api/smart_glasses/pair/start")).json()
    await admin.post(
        "/api/smart_glasses/pair/approve",
        json={"code": start["code"], "session_id": start["session_id"]},
    )
    body = await (await glasses.get(f"/api/smart_glasses/pair/{start['session_id']}/token")).json()
    return body["token"]


@pytest.mark.asyncio
async def test_invalid_token_is_unauthorized(
    hass_with_smart_glasses, hass_client_no_auth,
):
    glasses = await hass_client_no_auth()
    for path in [
        "/api/smart_glasses/glance/cards",
        "/api/smart_glasses/glance/states",
    ]:
        res = await glasses.get(path, headers={"Authorization": "Bearer not-a-token"})
        assert res.status == 401, f"{path} should require valid token"


@pytest.mark.asyncio
async def test_call_service_rejected_when_not_on_card(
    hass_with_smart_glasses, hass_client, hass_client_no_auth,
):
    """No cards configured → call_service rejects everything."""
    token = await _pair_and_get_token(hass_client_no_auth, hass_client)
    glasses = await hass_client_no_auth()
    res = await glasses.post(
        "/api/smart_glasses/glance/call_service",
        headers={"Authorization": f"Bearer {token}"},
        json={"domain": "light", "service": "turn_on", "target": {"entity_id": "light.kitchen"}},
    )
    assert res.status == 403


@pytest.mark.asyncio
async def test_call_service_allowed_when_action_pinned_to_card(
    hass_with_smart_glasses, hass, hass_client, hass_client_no_auth,
):
    """Pin a light.turn_off-against-light.kitchen action to a card and
    confirm the same call is now accepted."""
    admin = await hass_client()
    # Need a state to exist so the validator passes — set one.
    hass.states.async_set("light.kitchen", "off")

    seen_calls: list[ServiceCall] = []

    @callback
    def _handle_turn_off(call: ServiceCall) -> None:
        seen_calls.append(call)

    hass.services.async_register("light", "turn_off", _handle_turn_off)
    await hass.async_block_till_done()

    cards = [{
        "id": "c", "name": "C",
        "items": [{
            "type": "action", "name": "Off",
            "action": "light.turn_off", "target": "light.kitchen",
        }],
    }]
    set_resp = await admin.put("/api/smart_glasses/cards", json={"cards": cards})
    assert set_resp.status == 200, await set_resp.text()

    token = await _pair_and_get_token(hass_client_no_auth, hass_client)
    glasses = await hass_client_no_auth()

    # Allowed: exact match.
    ok = await glasses.post(
        "/api/smart_glasses/glance/call_service",
        headers={"Authorization": f"Bearer {token}"},
        json={"domain": "light", "service": "turn_off", "target": {"entity_id": "light.kitchen"}},
    )
    assert ok.status == 200, await ok.text()
    await hass.async_block_till_done()
    assert len(seen_calls) == 1
    assert seen_calls[0].data.get("entity_id") == "light.kitchen"

    # Rejected: same service, different target.
    bad = await glasses.post(
        "/api/smart_glasses/glance/call_service",
        headers={"Authorization": f"Bearer {token}"},
        json={"domain": "light", "service": "turn_off", "target": {"entity_id": "light.bedroom"}},
    )
    assert bad.status == 403


@pytest.mark.asyncio
async def test_blocked_service_rejected_even_with_card(
    hass_with_smart_glasses, hass, hass_client, hass_client_no_auth,
):
    """Even if an admin puts homeassistant.restart on a card, the
    glasses-side proxy must refuse."""
    admin = await hass_client()
    cards = [{
        "id": "c", "name": "C",
        "items": [{
            "type": "action", "name": "Reboot",
            "action": "homeassistant.restart",
        }],
    }]
    set_resp = await admin.put("/api/smart_glasses/cards", json={"cards": cards})
    assert set_resp.status == 200

    token = await _pair_and_get_token(hass_client_no_auth, hass_client)
    glasses = await hass_client_no_auth()

    res = await glasses.post(
        "/api/smart_glasses/glance/call_service",
        headers={"Authorization": f"Bearer {token}"},
        json={"domain": "homeassistant", "service": "restart"},
    )
    assert res.status == 403


@pytest.mark.asyncio
async def test_states_filtered_to_card_entities(
    hass_with_smart_glasses, hass, hass_client, hass_client_no_auth,
):
    """States endpoint must only return entities that appear on a card."""
    admin = await hass_client()
    hass.states.async_set("light.kitchen", "off")
    hass.states.async_set("light.bedroom", "on")
    hass.states.async_set("sensor.unused", "42")

    cards = [{
        "id": "c", "name": "C",
        "items": [{"type": "entity", "entity_id": "light.kitchen"}],
    }]
    await admin.put("/api/smart_glasses/cards", json={"cards": cards})

    token = await _pair_and_get_token(hass_client_no_auth, hass_client)
    glasses = await hass_client_no_auth()

    res = await glasses.get(
        "/api/smart_glasses/glance/states",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status == 200
    seen = {s["entity_id"] for s in await res.json()}
    assert seen == {"light.kitchen"}
