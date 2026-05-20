"""Static regression guard for the live panel search renderer."""

from __future__ import annotations

from pathlib import Path


def test_search_results_escape_entity_fields():
    """The live-search renderer must HTML-escape entity data before innerHTML."""
    panel_js = Path(__file__).resolve().parents[1] / "custom_components" / "smart_glasses" / "frontend" / "panel.js"
    content = panel_js.read_text(encoding="utf-8")

    assert 'const entityId = esc(s.entity_id);' in content
    assert 'const friendlyName = esc(s.attributes.friendly_name || s.entity_id);' in content
    assert 'const state = esc(s.state);' in content
    assert 'data-entity="${entityId}"' in content
    assert '<div class="entity-name">${friendlyName}</div>' in content
    assert '<div class="entity-id">${entityId} · ${state}</div>' in content