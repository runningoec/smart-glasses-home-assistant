"""HTTP views for the Smart Glasses integration.

Three audiences hit these endpoints:

1. **Glasses Web App** (no HA login). Calls:
   - ``GET  /smart-glasses-app``         — fetch the HTML to render
   - ``POST /api/smart_glasses/pair/start`` — request a pairing code
   - ``GET  /api/smart_glasses/pair/{session_id}/token`` — poll until approved
   - (After approval the glasses use the standard HA REST + websocket APIs
      with the long-lived token they were given. No additional view needed.)

2. **Phone / desktop browser** (HA-logged-in user). Calls:
   - ``GET  /api/smart_glasses/pairings`` — list pending + approved pairings
   - ``POST /api/smart_glasses/pair/approve`` — approve a code, mint LLAT
   - ``DELETE /api/smart_glasses/pair/{session_id}`` — revoke a pairing
   - ``GET  /api/smart_glasses/cards`` — get the current card list (JSON)
   - ``PUT  /api/smart_glasses/cards`` — save the card list (JSON)
   - ``GET  /api/smart_glasses/cards/yaml`` — get current cards as YAML
   - ``PUT  /api/smart_glasses/cards/yaml`` — save cards from a YAML body
     (handy for getting an AI to author your dashboard for you)

3. **HA frontend custom panel** loads ``frontend/panel.js`` directly via the
   route registered in ``__init__.py``.
"""

from __future__ import annotations

import logging
import secrets
import string
from datetime import timedelta
from typing import Any

import yaml
from aiohttp import web
from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import (
    API_PREFIX,
    DOMAIN,
    GLASSES_APP_URL,
    GLASSES_HTML_PATH,
    MAX_ENTITIES,
    PAIRING_CODE_LENGTH,
    PAIRING_TTL_SECONDS,
    PANEL_JS_PATH,
    PANEL_JS_ROUTE,
)
from .store import SmartGlassesStore

_LOGGER = logging.getLogger(__name__)

# Token name shown in HA's Long-Lived Access Token list, helping the user
# identify which pairing each token belongs to.
_TOKEN_PREFIX = "Smart Glasses pairing "

# Excludes ambiguous characters that look alike on small displays (O/0, I/1).
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))


def _store(hass: HomeAssistant) -> SmartGlassesStore:
    return hass.data[DOMAIN]["store"]


def _validate_cards(hass: HomeAssistant, cards: Any) -> str | None:
    """Validate a parsed cards list. Returns None on success, or an error
    string ready to surface to the user. Shared between the JSON and YAML
    endpoints so both reject the same way."""
    if not isinstance(cards, list):
        return "cards must be a list"
    for card in cards:
        if not isinstance(card, dict):
            return "each card must be an object"
        if "id" not in card or "name" not in card or "items" not in card:
            return "card must have id, name, items"
        items = card["items"]
        if not isinstance(items, list):
            return "card items must be a list"
        if len(items) > MAX_ENTITIES:
            return f"max {MAX_ENTITIES} items per card"
        for item in items:
            if not isinstance(item, dict) or "type" not in item:
                return "item must be an object with a type"
            if item["type"] == "entity":
                eid = item.get("entity_id")
                if not eid or not isinstance(eid, str):
                    return "entity item requires entity_id string"
                if hass.states.get(eid) is None:
                    return f"unknown entity_id: {eid}"
            elif item["type"] == "action":
                if not item.get("action") or not isinstance(item["action"], str):
                    return "action item requires action string"
                if not item.get("name") or not isinstance(item["name"], str):
                    return "action item requires name string"
                if "target" in item and not isinstance(item["target"], str):
                    return "action target must be a string"
            else:
                return f"unknown item type: {item['type']}"
    return None


# ---------------------------------------------------------------------------
# Glasses-facing views (no HA auth)
# ---------------------------------------------------------------------------


class GlassesAppView(HomeAssistantView):
    """Serve the glasses-side Web App HTML. This is the URL you register with
    Meta as your Web App. The page itself handles pairing + grid render."""

    url = GLASSES_APP_URL
    name = f"{DOMAIN}:app"
    requires_auth = False

    async def get(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(
            GLASSES_HTML_PATH,
            headers={"cache-control": "no-store, no-cache, must-revalidate"},
        )


class PanelJsView(HomeAssistantView):
    """Serve the management-panel JS bundle.

    We can't use StaticPathConfig for this file because StaticPathConfig
    doesn't let us set Cache-Control. With a CDN (Cloudflare Tunnel etc.)
    in front of HA, the CDN's default Edge Cache TTL caches panel.js for
    hours — making integration updates invisible to the browser until the
    edge cache expires. Serving via a view lets us send no-store, which
    Cloudflare honors. We also accept a ?v=<version> cache-buster on the
    URL for belt-and-suspenders.
    """

    url = PANEL_JS_ROUTE
    name = f"{DOMAIN}:panel_js"
    requires_auth = False

    async def get(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(
            PANEL_JS_PATH,
            headers={
                "cache-control": "no-store, no-cache, must-revalidate",
                "pragma": "no-cache",
                "content-type": "application/javascript",
            },
        )


class PairStartView(HomeAssistantView):
    """Glasses request a pairing session. Returns sessionId + short code."""

    url = f"{API_PREFIX}/pair/start"
    name = f"{DOMAIN}:pair_start"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store = _store(hass)

        # Prune any expired-and-unapproved sessions so the user doesn't
        # accumulate dead pairings just by leaving the app open.
        now = request.loop.time()
        # Approved pairings stay forever (revoke via panel); only expire unapproved.
        # Storage stores epoch in created_at, not loop time — use time.time() instead.
        import time as _time

        for sid, p in list(store.pairings.items()):
            if p["approved_at"] is None and _time.time() - p["created_at"] > PAIRING_TTL_SECONDS:
                await store.async_delete_pairing(sid)

        session_id = secrets.token_urlsafe(18)
        code = _new_code()
        await store.async_create_pairing(session_id, code)
        return self.json({
            "session_id": session_id,
            "code": code,
            "expires_in": PAIRING_TTL_SECONDS,
        })


class PairTokenView(HomeAssistantView):
    """Glasses poll this until the pairing is approved. Returns 202 while
    pending, 200 with the token once approved, 404 if the session is unknown
    (e.g. expired and pruned)."""

    url = f"{API_PREFIX}/pair/{{session_id}}/token"
    name = f"{DOMAIN}:pair_token"
    requires_auth = False

    async def get(self, request: web.Request, session_id: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        p = _store(hass).get_pairing(session_id)
        if not p:
            return self.json_message("unknown session", status_code=404)
        if not p["token"]:
            return self.json({"status": "pending"}, status_code=202)
        return self.json({
            "status": "approved",
            "token": p["token"],
            "user_id": p["user_id"],
            "approved_at": p["approved_at"],
        })


# ---------------------------------------------------------------------------
# Panel-facing views (HA auth required)
# ---------------------------------------------------------------------------


class PairingsListView(HomeAssistantView):
    """List pairings — both pending (no token) and approved."""

    url = f"{API_PREFIX}/pairings"
    name = f"{DOMAIN}:pairings_list"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        # Strip the token before returning. The phone-side UI only needs to
        # see WHICH pairings exist + their approval state, never the secret.
        sanitized = []
        for p in _store(hass).pairings.values():
            sanitized.append({
                "session_id": p["session_id"],
                "code": p["code"],
                "approved": bool(p["token"]),
                "user_id": p["user_id"],
                "created_at": p["created_at"],
                "approved_at": p["approved_at"],
            })
        return self.json({"pairings": sanitized})


class PairApproveView(HomeAssistantView):
    """Approve a pending pairing by its short code. Mints an LLAT for the
    user making this call, stashes it on the pairing record, and returns
    success. The glasses pick the token up via PairTokenView on their next
    poll."""

    url = f"{API_PREFIX}/pair/approve"
    name = f"{DOMAIN}:pair_approve"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        body = await request.json()
        code = (body.get("code") or "").upper().strip()
        if not code:
            return self.json_message("missing code", status_code=400)

        store = _store(hass)
        pairing = store.find_pairing_by_code(code)
        if not pairing:
            return self.json_message("no pairing for code", status_code=404)
        if pairing["token"]:
            return self.json_message("pairing already approved", status_code=409)

        # The approving user is the one HA's auth middleware attached.
        user = request["hass_user"]
        if user is None:
            return self.json_message("no user", status_code=401)

        try:
            refresh_token = await hass.auth.async_create_refresh_token(
                user,
                client_name=f"{_TOKEN_PREFIX}{pairing['session_id'][:8]}",
                token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN,
                access_token_expiration=timedelta(days=3650),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("LLAT creation failed")
            return self.json_message(f"token mint failed: {err}", status_code=500)

        access_token = hass.auth.async_create_access_token(refresh_token)
        await store.async_approve_pairing(
            session_id=pairing["session_id"],
            user_id=user.id,
            refresh_id=refresh_token.id,
            token=access_token,
        )
        return self.json({"ok": True, "session_id": pairing["session_id"]})


class PairRevokeView(HomeAssistantView):
    """Revoke a pairing: delete the refresh token in HA's auth manager (which
    invalidates any access token derived from it) and remove the local
    record."""

    url = f"{API_PREFIX}/pair/{{session_id}}"
    name = f"{DOMAIN}:pair_revoke"
    requires_auth = True

    async def delete(self, request: web.Request, session_id: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store = _store(hass)
        p = store.get_pairing(session_id)
        if not p:
            return self.json_message("unknown session", status_code=404)
        if p.get("refresh_id"):
            refresh = await hass.auth.async_get_refresh_token(p["refresh_id"])
            if refresh is not None:
                await hass.auth.async_remove_refresh_token(refresh)
        await store.async_delete_pairing(session_id)
        return self.json({"ok": True})


class CardsView(HomeAssistantView):
    """Get or replace the list of cards the glasses should glance.

    GET → returns ``{cards: [...]}``
    PUT body ``{cards: [...]}`` → replaces; validates size.
    """

    url = f"{API_PREFIX}/cards"
    name = f"{DOMAIN}:cards"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        return self.json({"cards": _store(hass).cards})

    async def put(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        body: dict[str, Any] = await request.json()
        cards = body.get("cards")
        err = _validate_cards(hass, cards)
        if err is not None:
            return self.json_message(err, status_code=400)
        await _store(hass).async_set_cards(cards)
        return self.json({"ok": True, "cards": cards})


class CardsYamlView(HomeAssistantView):
    """YAML interface to the card list — lets a user (or an AI assistant)
    edit the dashboard as a single text blob.

    GET → returns ``text/yaml`` of ``{cards: [...]}``.
    PUT body (any content-type, parsed as YAML) → replaces cards. Accepts
    either ``{cards: [...]}`` at the top level or just a bare ``[...]``.
    Validation is identical to :class:`CardsView` so behaviour stays consistent.
    """

    url = f"{API_PREFIX}/cards/yaml"
    name = f"{DOMAIN}:cards_yaml"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        cards = _store(hass).cards
        text = yaml.safe_dump(
            {"cards": cards},
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )
        return web.Response(text=text, content_type="text/yaml")

    async def put(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        text = await request.text()
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as err:
            return self.json_message(f"invalid yaml: {err}", status_code=400)
        # Accept either {cards: [...]} or a bare [...] for convenience.
        cards = parsed.get("cards") if isinstance(parsed, dict) else parsed
        err_msg = _validate_cards(hass, cards)
        if err_msg is not None:
            return self.json_message(err_msg, status_code=400)
        await _store(hass).async_set_cards(cards)
        return self.json({"ok": True, "cards": cards})


ALL_VIEWS: list[type[HomeAssistantView]] = [
    GlassesAppView,
    PanelJsView,
    PairStartView,
    PairTokenView,
    PairingsListView,
    PairApproveView,
    PairRevokeView,
    CardsView,
    CardsYamlView,
]
