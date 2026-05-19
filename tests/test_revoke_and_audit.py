"""HA-integration tests for the lifecycle endpoints: revoke and the
audit log.

* Revoking a pairing must invalidate the glasses-side token. The next
  glance call gets 401.
* Each significant action (approve, revoke, save cards) should leave a
  fresh entry at the head of the audit log.
"""

from __future__ import annotations

import pytest


async def _pair_and_get_token(hass_client_no_auth, hass_client) -> tuple[str, str]:
    """Run the pair handshake and return (session_id, plaintext_token)."""
    glasses = await hass_client_no_auth()
    admin = await hass_client()

    start = await (await glasses.post("/api/smart_glasses/pair/start")).json()
    sid = start["session_id"]
    await admin.post(
        "/api/smart_glasses/pair/approve",
        json={"code": start["code"], "session_id": sid},
    )
    body = await (await glasses.get(f"/api/smart_glasses/pair/{sid}/token")).json()
    return sid, body["token"]


# ---- revoke ------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_invalidates_token(
    hass_with_smart_glasses, hass_client, hass_client_no_auth,
):
    sid, token = await _pair_and_get_token(hass_client_no_auth, hass_client)
    glasses = await hass_client_no_auth()

    # Token works before revoke.
    pre = await glasses.get(
        "/api/smart_glasses/glance/cards",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert pre.status == 200

    admin = await hass_client()
    revoke = await admin.delete(f"/api/smart_glasses/pair/{sid}")
    assert revoke.status == 200

    # Token rejected after revoke.
    post = await glasses.get(
        "/api/smart_glasses/glance/cards",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert post.status == 401


@pytest.mark.asyncio
async def test_revoke_unknown_session_404(hass_with_smart_glasses, hass_client):
    admin = await hass_client()
    resp = await admin.delete("/api/smart_glasses/pair/does-not-exist")
    assert resp.status == 404


# ---- audit log ---------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_records_pair_approved_and_revoked(
    hass_with_smart_glasses, hass_client, hass_client_no_auth,
):
    admin = await hass_client()
    sid, _ = await _pair_and_get_token(hass_client_no_auth, hass_client)

    audit_after_approve = await (await admin.get("/api/smart_glasses/audit")).json()
    actions = [e["action"] for e in audit_after_approve["audit"]]
    assert "pair_approved" in actions

    await admin.delete(f"/api/smart_glasses/pair/{sid}")
    audit_after_revoke = await (await admin.get("/api/smart_glasses/audit")).json()
    actions = [e["action"] for e in audit_after_revoke["audit"]]
    assert actions[0] == "pair_revoked"
    assert "pair_approved" in actions


@pytest.mark.asyncio
async def test_audit_records_cards_saved(hass_with_smart_glasses, hass, hass_client):
    admin = await hass_client()
    hass.states.async_set("light.kitchen", "off")
    cards = [{
        "id": "c", "name": "C",
        "items": [{"type": "entity", "entity_id": "light.kitchen"}],
    }]
    save = await admin.put("/api/smart_glasses/cards", json={"cards": cards})
    assert save.status == 200

    audit = (await (await admin.get("/api/smart_glasses/audit")).json())["audit"]
    assert audit[0]["action"] == "cards_saved"
    assert audit[0]["card_count"] == 1
    assert audit[0]["total_items"] == 1
    assert audit[0]["source"] == "json"


@pytest.mark.asyncio
async def test_audit_excludes_tokens(
    hass_with_smart_glasses, hass_client, hass_client_no_auth,
):
    """The audit log must never include the plaintext token or its hash."""
    await _pair_and_get_token(hass_client_no_auth, hass_client)
    admin = await hass_client()
    audit = (await (await admin.get("/api/smart_glasses/audit")).json())["audit"]
    for entry in audit:
        assert "token" not in entry
        assert "token_hash" not in entry
        assert "token_pickup" not in entry
        assert "refresh_id" not in entry
