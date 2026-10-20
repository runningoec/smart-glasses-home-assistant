"""Static regression guards for the glasses WebMCP (Meta AI voice) tools."""

from __future__ import annotations

import re
from pathlib import Path

GLASSES_HTML = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "smart_glasses"
    / "frontend"
    / "glasses.html"
)


def test_voice_tools_registered_and_torn_down():
    """Tools register once the grid is live and unregister on re-pair."""
    content = GLASSES_HTML.read_text(encoding="utf-8")

    for name in ("list_home_items", "show_card", "activate_item"):
        assert f'name: "{name}"' in content
    assert "registerVoiceTools();" in content
    assert "unregisterVoiceTools();" in content


def test_confirm_gated_items_are_only_armed_by_voice():
    """A confirm item must return before the service call is reached."""
    content = GLASSES_HTML.read_text(encoding="utf-8")
    execute = content[content.index('name: "activate_item"') :]
    confirm_at = execute.index("if (shouldConfirm(hit.item))")
    awaiting_at = execute.index('status: "awaiting_confirmation"')
    call_at = execute.index("await callService(")
    assert confirm_at < awaiting_at < call_at


def test_glasses_only_fetch_integration_endpoints():
    """Voice tools must not reach HA APIs outside the scoped glance proxy."""
    content = GLASSES_HTML.read_text(encoding="utf-8")
    urls = re.findall(r'fetch\(\s*[`"]([^`"]+)', content)
    assert urls
    assert all(u.startswith("/api/smart_glasses/") for u in urls), urls
