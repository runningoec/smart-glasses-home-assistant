"""Tests for derived light-group state sent to the glasses."""

from __future__ import annotations

import pytest

from custom_components.smart_glasses.views import (
    _augment_with_group_meta,
    _glance_state_payloads_for_change,
)

# Keep tests runnable on Windows for the same reason documented elsewhere.
pytestmark = pytest.mark.enable_socket


@pytest.mark.asyncio
async def test_group_meta_leans_on_for_tie(hass):
    hass.states.async_set("light.kitchen_1", "on")
    hass.states.async_set("light.kitchen_2", "off")
    hass.states.async_set("light.kitchen_3", "unavailable")
    hass.states.async_set(
        "light.kitchen",
        "unknown",
        {"entity_id": ["light.kitchen_1", "light.kitchen_2", "light.kitchen_3"]},
    )
    await hass.async_block_till_done()

    payload = _augment_with_group_meta(hass, hass.states.get("light.kitchen").as_dict())

    assert payload["_smart_glasses"] == {
        "is_group": True,
        "members": 3,
        "on": 1,
        "off": 1,
        "unreachable": 1,
        "derived_state": "on",
    }


@pytest.mark.asyncio
async def test_group_meta_is_none_when_all_members_unreachable(hass):
    hass.states.async_set("light.office_1", "unknown")
    hass.states.async_set("light.office_2", "unavailable")
    hass.states.async_set(
        "light.office",
        "unknown",
        {"entity_id": ["light.office_1", "light.office_2"]},
    )
    await hass.async_block_till_done()

    payload = _augment_with_group_meta(hass, hass.states.get("light.office").as_dict())

    assert payload["_smart_glasses"]["derived_state"] is None
    assert payload["_smart_glasses"]["unreachable"] == 2


@pytest.mark.asyncio
async def test_member_change_refreshes_both_member_and_pinned_group(hass):
    hass.states.async_set("light.room_member", "on")
    hass.states.async_set("light.room_other", "off")
    hass.states.async_set(
        "light.room_group",
        "unknown",
        {"entity_id": ["light.room_member", "light.room_other"]},
    )
    await hass.async_block_till_done()

    cards = [{
        "id": "room",
        "name": "Room",
        "items": [
            {"type": "entity", "entity_id": "light.room_member"},
            {"type": "entity", "entity_id": "light.room_group"},
        ],
    }]

    payloads = _glance_state_payloads_for_change(
        hass,
        cards,
        "light.room_member",
        hass.states.get("light.room_member"),
    )

    assert [payload["entity_id"] for payload in payloads] == [
        "light.room_member",
        "light.room_group",
    ]
    assert payloads[1]["new_state"]["_smart_glasses"]["derived_state"] == "on"