"""Scope check for the glasses-side call_service proxy.

This is the function that decides whether a token leaked from the glasses
can do anything beyond "toggle entities pinned to a card". Bugs here are
high-blast-radius, so it's the first thing we cover with tests.
"""

from __future__ import annotations

from custom_components.smart_glasses.views import _service_call_allowed


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


# ---- general negatives -------------------------------------------------


def test_unknown_service_rejected():
    assert not _service_call_allowed(_entity_card("light.kitchen"),
                                     "system_log", "write", None)


def test_empty_domain_rejected():
    assert not _service_call_allowed(_entity_card("light.kitchen"),
                                     "", "toggle", "light.kitchen")


def test_empty_cards_rejects_all():
    assert not _service_call_allowed([], "homeassistant", "toggle", "light.kitchen")
