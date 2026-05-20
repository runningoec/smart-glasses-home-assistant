"""Regression tests for client-visible error sanitization."""

from __future__ import annotations

import pytest

from custom_components.smart_glasses.const import DOMAIN


@pytest.mark.asyncio
async def test_pair_approve_hides_internal_errors(
    hass_with_smart_glasses,
    hass_client,
    hass_client_no_auth,
    monkeypatch,
):
    """Approval failures should not leak exception class names or text."""
    glasses = await hass_client_no_auth()
    admin = await hass_client()
    start = await (await glasses.post("/api/smart_glasses/pair/start")).json()

    store = hass_with_smart_glasses.data[DOMAIN]["store"]

    async def boom(*args, **kwargs):
        raise RuntimeError("sensitive approval details")

    monkeypatch.setattr(store, "async_approve_pairing", boom)

    resp = await admin.post(
        "/api/smart_glasses/pair/approve",
        json={"code": start["code"], "session_id": start["session_id"]},
    )
    assert resp.status == 500
    body = await resp.json()
    assert body["message"] == "approve failed"


@pytest.mark.asyncio
async def test_glance_call_service_hides_internal_errors(
    hass_with_smart_glasses,
    hass,
    hass_client,
    hass_client_no_auth,
    monkeypatch,
):
    """Scoped service-call failures should not echo server exception text."""
    hass.states.async_set("light.kitchen", "off")
    admin = await hass_client()
    glasses = await hass_client_no_auth()

    cards = [{
        "id": "home",
        "name": "Home",
        "items": [{"type": "entity", "entity_id": "light.kitchen"}],
    }]
    set_resp = await admin.put("/api/smart_glasses/cards", json={"cards": cards})
    assert set_resp.status == 200

    start = await (await glasses.post("/api/smart_glasses/pair/start")).json()
    approve = await admin.post(
        "/api/smart_glasses/pair/approve",
        json={"code": start["code"], "session_id": start["session_id"]},
    )
    assert approve.status == 200
    token = (await (await glasses.get(
        f"/api/smart_glasses/pair/{start['session_id']}/token"
    )).json())["token"]

    async def boom(*args, **kwargs):
        raise RuntimeError("service failure details")

    monkeypatch.setattr(hass.services, "async_call", boom)

    resp = await glasses.post(
        "/api/smart_glasses/glance/call_service",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "domain": "homeassistant",
            "service": "toggle",
            "target": {"entity_id": "light.kitchen"},
        },
    )
    assert resp.status == 500
    body = await resp.json()
    assert body["message"] == "call_service failed"


@pytest.mark.asyncio
async def test_cards_yaml_hides_parser_details(hass_with_smart_glasses, hass_client):
    """YAML parse failures should stay generic on the wire."""
    admin = await hass_client()
    resp = await admin.put(
        "/api/smart_glasses/cards/yaml",
        data="cards: [\n",
        headers={"Content-Type": "text/yaml"},
    )
    assert resp.status == 400
    body = await resp.json()
    assert body["message"] == "invalid yaml"
