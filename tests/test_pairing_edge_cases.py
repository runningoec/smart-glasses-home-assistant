"""Pairing flow edge cases the happy-path tests don't cover.

* Double-approving a pairing → 409.
* Approving an unknown code → 404.
* Approving with a wrong-shaped body → 400.
* Pending pairings show up in /pairings; approving moves them.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_double_approve_returns_409(
    hass_with_smart_glasses, hass_client, hass_client_no_auth,
):
    glasses = await hass_client_no_auth()
    admin = await hass_client()

    start = await (await glasses.post("/api/smart_glasses/pair/start")).json()
    payload = {"code": start["code"], "session_id": start["session_id"]}

    first = await admin.post("/api/smart_glasses/pair/approve", json=payload)
    assert first.status == 200

    second = await admin.post("/api/smart_glasses/pair/approve", json=payload)
    assert second.status == 409


@pytest.mark.asyncio
async def test_approve_unknown_code_returns_404(
    hass_with_smart_glasses, hass_client, hass_client_no_auth,
):
    glasses = await hass_client_no_auth()
    admin = await hass_client()

    start = await (await glasses.post("/api/smart_glasses/pair/start")).json()
    # Real session_id, but the code doesn't match what the server stored.
    resp = await admin.post(
        "/api/smart_glasses/pair/approve",
        json={"code": "ZZZZZZ", "session_id": start["session_id"]},
    )
    assert resp.status == 404


@pytest.mark.asyncio
async def test_approve_missing_code_returns_400(hass_with_smart_glasses, hass_client):
    admin = await hass_client()
    resp = await admin.post(
        "/api/smart_glasses/pair/approve",
        json={"session_id": "x"},
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_pairings_list_shows_pending_then_approved(
    hass_with_smart_glasses, hass_client, hass_client_no_auth,
):
    glasses = await hass_client_no_auth()
    admin = await hass_client()

    start = await (await glasses.post("/api/smart_glasses/pair/start")).json()
    listed = (await (await admin.get("/api/smart_glasses/pairings")).json())["pairings"]
    by_sid = {p["session_id"]: p for p in listed}
    assert by_sid[start["session_id"]]["approved"] is False
    assert by_sid[start["session_id"]]["code"] == start["code"]

    await admin.post(
        "/api/smart_glasses/pair/approve",
        json={"code": start["code"], "session_id": start["session_id"]},
    )
    listed = (await (await admin.get("/api/smart_glasses/pairings")).json())["pairings"]
    by_sid = {p["session_id"]: p for p in listed}
    assert by_sid[start["session_id"]]["approved"] is True
    # Server-side never exposes the token itself in the list response.
    assert "token" not in by_sid[start["session_id"]]
    assert "token_hash" not in by_sid[start["session_id"]]
