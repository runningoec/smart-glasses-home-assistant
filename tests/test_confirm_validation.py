"""Pure-logic tests for the ``confirm`` field validator on card items.

Confirm can be:
  * absent / None / False  → no confirmation needed
  * True                    → always require confirm
  * {after?: 'HH:MM', before?: 'HH:MM'} → require confirm in the window

We test every accepted shape and the common ways to get it wrong.
"""

from __future__ import annotations

import pytest

from custom_components.smart_glasses.views import _validate_confirm

# pytest-homeassistant-custom-component disables sockets, but the asyncio
# event loop opens a self-pipe at construction. Re-enable for this module.
pytestmark = pytest.mark.enable_socket


# ---- accepted shapes ---------------------------------------------------


def test_none_allowed():
    assert _validate_confirm(None) is None


def test_true_allowed():
    assert _validate_confirm(True) is None


def test_false_allowed():
    assert _validate_confirm(False) is None


def test_after_only():
    assert _validate_confirm({"after": "22:00"}) is None


def test_before_only():
    assert _validate_confirm({"before": "07:00"}) is None


def test_window_same_day():
    assert _validate_confirm({"after": "07:00", "before": "22:00"}) is None


def test_window_wraps_midnight():
    # after > before is allowed — interpreted as "after 22:00 OR before 07:00"
    assert _validate_confirm({"after": "22:00", "before": "07:00"}) is None


def test_midnight_boundaries():
    assert _validate_confirm({"after": "00:00", "before": "23:59"}) is None


# ---- rejected shapes ---------------------------------------------------


def test_empty_dict_rejected():
    msg = _validate_confirm({})
    assert msg and "ambiguous" in msg


def test_string_rejected():
    msg = _validate_confirm("always")
    assert msg and "boolean" in msg


def test_int_rejected():
    msg = _validate_confirm(1)
    # ints are bool-compatible in Python (True/False ARE int) so an int
    # OTHER than 0/1 is technically `isinstance(_, bool) is False`. But
    # bool is a subclass of int — make sure we don't accidentally allow
    # the int form. (The validator's isinstance(value, bool) only matches
    # True/False, not 2.)
    assert msg and "boolean" in msg


def test_unknown_key_rejected():
    msg = _validate_confirm({"between": "22:00"})
    assert msg and "unknown key" in msg


def test_after_not_string_rejected():
    msg = _validate_confirm({"after": 2200})
    assert msg and "HH:MM" in msg


def test_after_malformed_rejected():
    for bad in ["", "22", "22:", "22:0", "2:00", "25:00", "22:60", "abc"]:
        assert _validate_confirm({"after": bad}) is not None, (
            f"{bad!r} should be rejected"
        )


def test_before_malformed_rejected():
    msg = _validate_confirm({"before": "noon"})
    assert msg and "HH:MM" in msg
