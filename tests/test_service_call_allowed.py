"""Scope check for the glasses-side call_service proxy.

This is the function that decides whether a token leaked from the glasses
can do anything beyond "toggle entities pinned to a card". Bugs here are
high-blast-radius, so it's the first thing we cover with tests.
"""

from __future__ import annotations

import pytest

from custom_components.smart_glasses.views import _service_call_allowed

# pytest-homeassistant-custom-component disables sockets, but the asyncio
# event loop opens a self-pipe socket as soon as it's instantiated. Allow
# sockets for this module so even pure-logic tests can run.
pytestmark = pytest.mark.enable_socket


def _entity_card(entity_id: str) -> list[dict]:
    return [{
        "id": "c", "name": "C",
        "items": [{"type": "entity", "entity_id": entity_id}],
    }]


def _action_card(name: str, action: str, target: str | None = None) -> list[dict]:
    item: dict = {"type": "action", "name": name, "action": action}
    if target is not None:
        item["target"] = target
    return [{"id": "c", "name": "C", "items": [item]}]


# ---- toggle path -------------------------------------------------------


def test_toggle_allowed_for_entity_on_card():
    assert _service_call_allowed(_entity_card("light.kitchen"),
                                 "homeassistant", "toggle", "light.kitchen")


def test_toggle_rejected_for_entity_not_on_card():
    assert not _service_call_allowed(_entity_card("light.kitchen"),
                                     "homeassistant", "toggle", "light.bedroom")


def test_toggle_rejected_when_no_target():
    assert not _service_call_allowed(_entity_card("light.kitchen"),
                                     "homeassistant", "toggle", None)


# ---- action item path --------------------------------------------------


def test_action_allowed_on_exact_match():
    cards = _action_card("All off", "light.turn_off", "group.house")
    assert _service_call_allowed(cards, "light", "turn_off", "group.house")


def test_action_rejected_on_target_mismatch():
    cards = _action_card("All off", "light.turn_off", "group.house")
    assert not _service_call_allowed(cards, "light", "turn_off", "group.garage")


def test_action_with_no_target_requires_no_target():
    cards = _action_card("Scene", "scene.activate")
    assert _service_call_allowed(cards, "scene", "activate", None)
    assert not _service_call_allowed(cards, "scene", "activate", "scene.morning")


# ---- the blocklist ----------------------------------------------------


def test_blocked_service_rejected_even_when_on_card():
    cards = _action_card("Reboot", "homeassistant.restart")
    assert not _service_call_allowed(cards, "homeassistant", "restart", None)


def test_blocked_recorder_purge_rejected():
    cards = _action_card("Purge", "recorder.purge")
    assert not _service_call_allowed(cards, "recorder", "purge", None)


def test_blocked_hassio_domain_rejected():
    cards = _action_card("Reboot", "hassio.host_reboot")
    assert not _service_call_allowed(cards, "hassio", "host_reboot", None)


def test_blocked_shell_command_domain_rejected():
    cards = _action_card("Run", "shell_command.echo")
    assert not _service_call_allowed(cards, "shell_command", "echo", None)


# ---- per-domain natural-action dispatch --------------------------------


def test_scene_turn_on_allowed_for_pinned_scene():
    """Tapping a scene cell fires scene.turn_on, not the no-op
    homeassistant.toggle. The proxy must accept it."""
    cards = _entity_card("scene.movie_time")
    assert _service_call_allowed(cards, "scene", "turn_on", "scene.movie_time")


def test_scene_toggle_rejected_even_for_pinned_scene():
    """Scene domain has no toggle service; rejecting noisy clients."""
    cards = _entity_card("scene.movie_time")
    assert not _service_call_allowed(cards, "scene", "toggle", "scene.movie_time")


def test_button_press_allowed_for_pinned_button():
    cards = _entity_card("button.doorbell")
    assert _service_call_allowed(cards, "button", "press", "button.doorbell")


def test_script_turn_on_allowed():
    cards = _entity_card("script.morning")
    assert _service_call_allowed(cards, "script", "turn_on", "script.morning")


def test_automation_trigger_allowed():
    cards = _entity_card("automation.bedtime")
    assert _service_call_allowed(cards, "automation", "trigger", "automation.bedtime")


def test_cover_open_close_allowed():
    cards = _entity_card("cover.garage_door")
    assert _service_call_allowed(cards, "cover", "open_cover", "cover.garage_door")
    assert _service_call_allowed(cards, "cover", "close_cover", "cover.garage_door")


def test_light_turn_on_off_explicitly_allowed():
    cards = _entity_card("light.kitchen")
    # All three are reasonable for a tap.
    assert _service_call_allowed(cards, "light", "turn_on",  "light.kitchen")
    assert _service_call_allowed(cards, "light", "turn_off", "light.kitchen")
    assert _service_call_allowed(cards, "light", "toggle",   "light.kitchen")
    # And generic toggle still works.
    assert _service_call_allowed(cards, "homeassistant", "toggle", "light.kitchen")


def test_group_toggle_allowed():
    """Light-Group-helper entities live under light.* and HA reports
    on/off normally. legacy group.* should also work via toggle."""
    assert _service_call_allowed(_entity_card("light.living_room_group"),
                                 "light", "toggle", "light.living_room_group")
    assert _service_call_allowed(_entity_card("group.house_lights"),
                                 "group", "toggle", "group.house_lights")


def test_cross_domain_service_rejected():
    """Pinning light.kitchen doesn't authorise scene.turn_on at it."""
    cards = _entity_card("light.kitchen")
    assert not _service_call_allowed(cards, "scene", "turn_on", "light.kitchen")


# ---- general negatives -------------------------------------------------


def test_unknown_service_rejected():
    assert not _service_call_allowed(_entity_card("light.kitchen"),
                                     "system_log", "write", None)


def test_empty_domain_rejected():
    assert not _service_call_allowed(_entity_card("light.kitchen"),
                                     "", "toggle", "light.kitchen")


def test_empty_cards_rejects_all():
    assert not _service_call_allowed([], "homeassistant", "toggle", "light.kitchen")
