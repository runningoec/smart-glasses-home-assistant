"""HA-integration test for the pairing flow end to end.

Walks the full sequence of HTTP calls a real glasses Web App + panel pair
exchanges, plus the post-approval glance API. These tests boot a real
HA instance via pytest-homeassistant-custom-component and exercise the
HTTP views through HA's test client.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_pair_start_returns_session_and_code(hass_with_smart_glasses, hass_client):
    """POST /pair/start returns an opaque session_id and a 6-char code."""
    client = await hass_client()
    res = await client.post("/api/smart_glasses/pair/start")
    assert res.status == 200
    body = await res.json()
    assert isinstance(body.get("session_id"), str) and len(body["session_id"]) >= 16
    assert isinstance(body.get("code"), str) and len(body["code"]) == 6
    assert body["expires_in"] > 0


@pytest.mark.asyncio
async def test_token_pending_then_approved_then_collected(
    hass_with_smart_glasses, hass_client, hass_client_no_auth,
):
    """The full handshake:

    1. Glasses (no HA auth) starts a pairing.
    2. Polling /pair/<id>/token returns 202 pending with the code echoed.
    3. Admin (HA auth) approves with (code, session_id).
    4. Glasses polls again — gets 200 with the plaintext token.
    5. A second poll gets 410: the pickup was wiped after first read.
    """
    glasses = await hass_client_no_auth()
    admin = await hass_client()

    # 1: start
    start = await glasses.post("/api/smart_glasses/pair/start")
    started = await start.json()
    sid = started["session_id"]
    code = started["code"]

    # 2: pending
    pending = await glasses.get(f"/api/smart_glasses/pair/{sid}/token")
    assert pending.status == 202
    assert (await pending.json())["code"] == code

    # 3: approve
    approve = await admin.post(
        "/api/smart_glasses/pair/approve",
        json={"code": code, "session_id": sid},
    )
    assert approve.status == 200, await approve.text()

    # 4: pickup
    pickup = await glasses.get(f"/api/smart_glasses/pair/{sid}/token")
    assert pickup.status == 200
    body = await pickup.json()
    assert body["status"] == "approved"
    token = body["token"]
    assert isinstance(token, str) and len(token) >= 32

    # 5: second pickup is gone
    again = await glasses.get(f"/api/smart_glasses/pair/{sid}/token")
    assert again.status == 410


@pytest.mark.asyncio
async def test_approve_with_wrong_session_id_rejected(
    hass_with_smart_glasses, hass_client, hass_client_no_auth,
):
    """An attacker who learns the code (e.g. shoulder-surf) but not the
    session_id should not be able to claim a pairing."""
    glasses = await hass_client_no_auth()
    admin = await hass_client()

    started = await (await glasses.post("/api/smart_glasses/pair/start")).json()

    # Right code, wrong session_id.
    res = await admin.post(
        "/api/smart_glasses/pair/approve",
        json={"code": started["code"], "session_id": "not-the-real-one"},
    )
    assert res.status == 404


@pytest.mark.asyncio
async def test_approve_missing_session_id_rejected(
    hass_with_smart_glasses, hass_client,
):
    """The bound-approval rule rejects code-only payloads."""
    res = await (await hass_client()).post(
        "/api/smart_glasses/pair/approve",
        json={"code": "ABCDEF"},
    )
    assert res.status == 400
