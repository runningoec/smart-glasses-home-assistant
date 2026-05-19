"""CSRF guard for the panel-side mutating endpoints.

We rely on Sec-Fetch-Site (modern browsers) with an Origin vs Host
fallback for older clients. Non-browser callers (curl, scripts) pass
through — they're not subject to browser-driven CSRF.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import make_mocked_request

from custom_components.smart_glasses.views import _csrf_guard

# pytest-homeassistant-custom-component disables sockets, but the asyncio
# event loop opens a self-pipe socket as soon as it's instantiated. Allow
# sockets for this module so even pure-logic tests can run.
pytestmark = pytest.mark.enable_socket


def _req(headers: dict[str, str]):
    return make_mocked_request("POST", "/api/smart_glasses/pair/approve", headers=headers)


# ---- Sec-Fetch-Site cases -----------------------------------------------


def test_same_origin_allowed():
    assert _csrf_guard(_req({"Sec-Fetch-Site": "same-origin"}))


def test_same_site_allowed():
    assert _csrf_guard(_req({"Sec-Fetch-Site": "same-site"}))


def test_browserless_none_allowed():
    # "none" is what browsers send for top-level navigations and direct
    # address-bar requests. Not a CSRF vector.
    assert _csrf_guard(_req({"Sec-Fetch-Site": "none"}))


def test_cross_site_rejected():
    assert not _csrf_guard(_req({"Sec-Fetch-Site": "cross-site"}))


# ---- Origin fallback ----------------------------------------------------


def test_origin_matches_host_allowed():
    assert _csrf_guard(_req({
        "Origin": "https://my-ha.example.com",
        "Host":   "my-ha.example.com",
    }))


def test_origin_differs_from_host_rejected():
    assert not _csrf_guard(_req({
        "Origin": "https://evil.example.com",
        "Host":   "my-ha.example.com",
    }))


def test_origin_scheme_does_not_confuse_match():
    # The scheme isn't compared (Origin is full URL, Host is netloc).
    assert _csrf_guard(_req({
        "Origin": "http://my-ha.example.com:8123",
        "Host":   "my-ha.example.com:8123",
    }))


# ---- non-browser callers ------------------------------------------------


def test_no_headers_allowed():
    # curl / scripts / integrations send neither header. Browser-driven
    # CSRF doesn't apply to them.
    assert _csrf_guard(_req({}))


def test_only_host_no_origin_allowed():
    assert _csrf_guard(_req({"Host": "my-ha.example.com"}))
