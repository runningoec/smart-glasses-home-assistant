"""Shared pytest fixtures for smart_glasses tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):  # noqa: PT004
    """Required by pytest-homeassistant-custom-component to make the
    integration importable from ``custom_components/``."""
    return
