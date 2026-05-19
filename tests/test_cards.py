"""HA-integration tests for the panel-facing /cards endpoint.

Covers the success path (round-trip a sensible config) and the failure
modes that are easy to hit by hand — wrong shapes, missing fields,
unknown entity_ids, oversized item lists.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_cards_round_trip(hass_with_smart_glasses, hass, hass_client):
    """GET initially empty; PUT a config; GET returns what we PUT."""
    client = await hass_client()
    hass.states.async_set("light.kitchen", "off")

    initial = await (await client.get("/api/smart_glasses/cards")).json()
    assert initial == {"cards": []}

    cards = [{
        "id": "home", "name": "Home",
        "items": [{"type": "entity", "entity_id": "light.kitchen"}],
    }]
    put_resp = await client.put("/api/smart_glasses/cards", json={"cards": cards})
    assert put_resp.status == 200

    after = await (await client.get("/api/smart_glasses/cards")).json()
    assert after == {"cards": cards}


@pytest.mark.asyncio
async def test_cards_unknown_entity_rejected(hass_with_smart_glasses, hass_client):
    client = await hass_client()
    cards = [{
        "id": "c", "name": "C",
        "items": [{"type": "entity", "entity_id": "light.does_not_exist"}],
    }]
    resp = await client.put("/api/smart_glasses/cards", json={"cards": cards})
    assert resp.status == 400
    body = await resp.json()
    assert "unknown entity_id" in body["message"]


@pytest.mark.asyncio
async def test_cards_max_items_enforced(hass_with_smart_glasses, hass, hass_client):
    """MAX_ENTITIES (8) is enforced per card."""
    client = await hass_client()
    for i in range(9):
        hass.states.async_set(f"light.l{i}", "off")
    cards = [{
        "id": "c", "name": "C",
        "items": [{"type": "entity", "entity_id": f"light.l{i}"} for i in range(9)],
    }]
    resp = await client.put("/api/smart_glasses/cards", json={"cards": cards})
    assert resp.status == 400
    body = await resp.json()
    assert "max" in body["message"]


@pytest.mark.asyncio
async def test_cards_action_with_invalid_confirm_rejected(
    hass_with_smart_glasses, hass_client,
):
    client = await hass_client()
    cards = [{
        "id": "c", "name": "C",
        "items": [{
            "type": "action", "name": "X", "action": "light.turn_off",
            "confirm": {"after": "not-a-time"},
        }],
    }]
    resp = await client.put("/api/smart_glasses/cards", json={"cards": cards})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_cards_yaml_round_trip(hass_with_smart_glasses, hass, hass_client):
    """The YAML endpoint round-trips through PyYAML and validates the
    same way the JSON endpoint does."""
    client = await hass_client()
    hass.states.async_set("cover.garage_door", "closed")

    yaml_in = """\
cards:
  - id: garage
    name: Garage
    items:
      - type: entity
        entity_id: cover.garage_door
        confirm: true
"""
    resp = await client.put(
        "/api/smart_glasses/cards/yaml",
        data=yaml_in,
        headers={"content-type": "text/yaml"},
    )
    assert resp.status == 200, await resp.text()

    # GET YAML — should serialise back to similar content.
    got = await (await client.get("/api/smart_glasses/cards/yaml")).text()
    assert "cover.garage_door" in got
    assert "confirm" in got


@pytest.mark.asyncio
async def test_cards_yaml_invalid_yaml_rejected(hass_with_smart_glasses, hass_client):
    client = await hass_client()
    resp = await client.put(
        "/api/smart_glasses/cards/yaml",
        data="cards: [ this is not valid yaml: at all",
        headers={"content-type": "text/yaml"},
    )
    assert resp.status == 400
    assert "invalid yaml" in (await resp.json())["message"]
