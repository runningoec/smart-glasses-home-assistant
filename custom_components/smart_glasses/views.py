"""HTTP views for the Smart Glasses integration.

Three audiences, three auth modes:

1. **Glasses Web App pairing-bootstrap** (no auth). The brief window during
   which a freshly-launched glasses app gets a pairing code and waits for it
   to be approved:
   - ``GET  /smart-glasses-app``                          — fetch the HTML
   - ``POST /api/smart_glasses/pair/start``               — request a code
   - ``GET  /api/smart_glasses/pair/{session_id}/token``  — poll for approval

2. **Glasses Web App day-to-day** (Bearer auth with our session token).
   After approval the glasses use these proxy endpoints — they never call
   HA's native /api/* anymore, so the token's scope is exactly "the entities
   and actions currently on a card":
   - ``GET  /api/smart_glasses/glance/cards``
   - ``GET  /api/smart_glasses/glance/states``    — only entities on cards
   - ``POST /api/smart_glasses/glance/call_service`` — only services on cards
   - ``WS   /api/smart_glasses/glance/ws``         — filtered state_changed

3. **Phone / desktop browser** (HA-logged-in user). The management surface:
   - ``GET  /api/smart_glasses/pairings``
   - ``POST /api/smart_glasses/pair/approve``     — bound to (code, session_id)
   - ``DELETE /api/smart_glasses/pair/{session_id}``
   - ``GET/PUT /api/smart_glasses/cards``
   - ``GET/PUT /api/smart_glasses/cards/yaml``
   - ``GET  /api/smart_glasses/audit``

The custom panel's JS bundle is served by ``PanelJsView`` with no-store
Cache-Control so CDN-fronted HA installs (Cloudflare etc.) pick up updates.
"""

from __future__ import annotations

import json
import logging
import secrets
import string
import time
from typing import Any

import yaml
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback

from .const import (
    API_PREFIX,
    DOMAIN,
    GLASSES_APP_URL,
    GLASSES_HTML_PATH,
    MAX_ENTITIES,
    MAX_PENDING_PAIRINGS,
    PAIR_START_PER_IP_PER_MIN,
    PAIRING_CODE_LENGTH,
    PAIRING_TTL_SECONDS,
    PANEL_JS_PATH,
    PANEL_JS_ROUTE,
)
from .store import SmartGlassesStore

_LOGGER = logging.getLogger(__name__)

# Excludes ambiguous characters that look alike on small displays (O/0, I/1).
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))


def _store(hass: HomeAssistant) -> SmartGlassesStore:
    return hass.data[DOMAIN]["store"]


# Sliding-window per-IP rate limit on /pair/start. In-memory; resets on HA
# restart. Sized for legitimate users (a few starts per minute at most) while
# making spam unprofitable.
_RATE_LIMIT_PAIR_START: dict[str, list[float]] = {}
_RATE_LIMIT_WINDOW_SEC = 60.0


def _rate_limit_check(ip: str) -> bool:
    """Return True if the request from this IP is within the budget.

    The dict only stores timestamps inside the current 60-second window, so
    memory stays bounded as long as the host of IPs hitting us is finite.
    """
    now = time.time()
    recent = [t for t in _RATE_LIMIT_PAIR_START.get(ip, []) if now - t < _RATE_LIMIT_WINDOW_SEC]
    if len(recent) >= PAIR_START_PER_IP_PER_MIN:
        _RATE_LIMIT_PAIR_START[ip] = recent
        return False
    recent.append(now)
    _RATE_LIMIT_PAIR_START[ip] = recent
    # Opportunistic eviction: every now and then, drop empty IP entries.
    if len(_RATE_LIMIT_PAIR_START) > 256:
        for k in [k for k, v in _RATE_LIMIT_PAIR_START.items() if not v]:
            _RATE_LIMIT_PAIR_START.pop(k, None)
    return True


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

        # Rate-limit before doing any work. Endpoint is unauthenticated and
        # internet-reachable, so a bored attacker could otherwise spam it.
        ip = request.remote or "unknown"
        if not _rate_limit_check(ip):
            return self.json_message(
                "too many pairing attempts; try again in a minute",
                status_code=429,
            )

        # Prune any expired-and-unapproved sessions so the user doesn't
        # accumulate dead pairings just by leaving the app open. Approved
        # pairings stay forever (revoke via panel) — only expire unapproved.
        for sid, p in list(store.pairings.items()):
            if p["approved_at"] is None and time.time() - p["created_at"] > PAIRING_TTL_SECONDS:
                await store.async_delete_pairing(sid)

        # Hard cap on pending sessions across the whole install. Stops a
        # spammer from running us out of disk by hammering /pair/start with
        # spoofed IPs (each individually under the rate limit).
        pending = sum(1 for p in store.pairings.values() if p["approved_at"] is None)
        if pending >= MAX_PENDING_PAIRINGS:
            return self.json_message(
                "too many pending pairings; ask an admin to revoke unused ones",
                status_code=503,
            )

        session_id = secrets.token_urlsafe(18)
        code = _new_code()
        await store.async_create_pairing(session_id, code)
        return self.json({
            "session_id": session_id,
            "code": code,
            "expires_in": PAIRING_TTL_SECONDS,
        })


class PairTokenView(HomeAssistantView):
    """Glasses poll this until the pairing is approved.

    Returns 202 while pending, 200 with the plaintext token on the FIRST
    poll after approval (and wipes the pickup), 410 Gone if the pickup has
    already been collected (caller should re-pair), and 404 if the session
    is unknown.
    """

    url = f"{API_PREFIX}/pair/{{session_id}}/token"
    name = f"{DOMAIN}:pair_token"
    requires_auth = False

    async def get(self, request: web.Request, session_id: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store = _store(hass)
        p = store.get_pairing(session_id)
        if not p:
            return self.json_message("unknown session", status_code=404)
        if not p.get("token_hash"):
            # Still waiting on approval. Include the code so a re-launched
            # glasses app can recover and keep displaying the SAME code
            # instead of churning /pair/start every page load.
            return self.json({"status": "pending", "code": p["code"]}, status_code=202)
        pickup = p.get("token_pickup")
        if not pickup:
            return self.json_message(
                "token already collected — re-pair to get a fresh one",
                status_code=410,
            )
        # Hand it over exactly once.
        await store.async_clear_pickup(session_id)
        return self.json({
            "status": "approved",
            "token": pickup,
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
                "approved": bool(p.get("token_hash")),
                "user_id": p.get("user_id"),
                "created_at": p["created_at"],
                "approved_at": p.get("approved_at"),
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
        session_id = (body.get("session_id") or "").strip()
        if not code:
            return self.json_message("missing code", status_code=400)
        if not session_id:
            return self.json_message("missing session_id", status_code=400)

        store = _store(hass)
        pairing = store.get_pairing(session_id)
        # Bind approval to (session_id, code) — both must come from the same
        # pairing record. Stops a stolen code alone from being claimed.
        if not pairing or pairing.get("code") != code:
            return self.json_message("no pairing matches code+session_id", status_code=404)
        if pairing["token"]:
            return self.json_message("pairing already approved", status_code=409)

        # The approving user is the one HA's auth middleware attached.
        user = request["hass_user"]
        if user is None:
            return self.json_message("no user", status_code=401)

        # Random opaque session token — ~256 bits. Hashed at rest, kept only
        # on the glasses' localStorage in plaintext after the one-shot pickup.
        token = secrets.token_urlsafe(32)
        await store.async_approve_pairing(
            session_id=pairing["session_id"],
            user_id=user.id,
            token=token,
        )
        await store.async_audit(
            "pair_approved",
            user_id=user.id,
            user_name=user.name,
            session_id=pairing["session_id"],
            code=pairing["code"],
        )
        return self.json({"ok": True, "session_id": pairing["session_id"]})


class PairRevokeView(HomeAssistantView):
    """Revoke a pairing: drop our local record. Because the session token
    only exists in our store (as a hash) and on the glasses' localStorage,
    deleting our record invalidates the token immediately — no separate
    auth-manager dance like in the legacy LLAT design."""

    url = f"{API_PREFIX}/pair/{{session_id}}"
    name = f"{DOMAIN}:pair_revoke"
    requires_auth = True

    async def delete(self, request: web.Request, session_id: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store = _store(hass)
        p = store.get_pairing(session_id)
        if not p:
            return self.json_message("unknown session", status_code=404)
        had_token = bool(p.get("token_hash"))
        await store.async_delete_pairing(session_id)
        user = request["hass_user"]
        await store.async_audit(
            "pair_revoked",
            user_id=getattr(user, "id", None),
            user_name=getattr(user, "name", None),
            session_id=session_id,
            code=p.get("code"),
            had_token=had_token,
        )
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
        store = _store(hass)
        await store.async_set_cards(cards)
        user = request["hass_user"]
        await store.async_audit(
            "cards_saved",
            user_id=getattr(user, "id", None),
            user_name=getattr(user, "name", None),
            card_count=len(cards),
            total_items=sum(len(c.get("items", [])) for c in cards),
            source="json",
        )
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
        store = _store(hass)
        await store.async_set_cards(cards)
        user = request["hass_user"]
        await store.async_audit(
            "cards_saved",
            user_id=getattr(user, "id", None),
            user_name=getattr(user, "name", None),
            card_count=len(cards),
            total_items=sum(len(c.get("items", [])) for c in cards),
            source="yaml",
        )
        return self.json({"ok": True, "cards": cards})


# ---------------------------------------------------------------------------
# Glasses-facing proxy views — authenticated via the session token issued at
# approval. The glasses never call HA's native /api/* directly any more, so
# the token's blast radius is limited to what these endpoints expose.
# ---------------------------------------------------------------------------


def _glasses_pairing(request: web.Request, hass: HomeAssistant) -> dict[str, Any] | None:
    """Authenticate a glasses request. Returns the pairing dict on success,
    None on failure. Caller is responsible for sending 401 if None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:].strip()
    if not token:
        return None
    return _store(hass).find_pairing_by_token(token)


def _card_entity_ids(cards: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for c in cards:
        for item in c.get("items", []):
            if item.get("type") == "entity" and isinstance(item.get("entity_id"), str):
                out.add(item["entity_id"])
    return out


def _service_call_allowed(
    cards: list[dict[str, Any]], domain: str, service: str, target_eid: str | None
) -> bool:
    """A service call is only permitted if it matches something currently
    on a card. Two cases:

    - ``homeassistant.toggle`` against an ``entity`` item with the same
      ``entity_id`` (the implicit "tap the cell" action).
    - A ``call_service`` whose ``domain.service`` and ``target`` exactly
      match an ``action`` item on some card.

    Anything else is rejected — the token can't be used to fire arbitrary
    HA services even if it's leaked from the glasses.
    """
    if not domain or not service:
        return False
    service_str = f"{domain}.{service}"
    for card in cards:
        for item in card.get("items", []):
            t = item.get("type")
            if t == "action" and item.get("action") == service_str:
                # `None == None` covers the "no-target action, no-target call".
                if item.get("target") == target_eid:
                    return True
            elif t == "entity" and service_str == "homeassistant.toggle":
                if item.get("entity_id") == target_eid:
                    return True
    return False


class GlanceCardsView(HomeAssistantView):
    """Cards endpoint for the glasses — same payload as the panel's /cards
    but auth is the glasses session token, not HA login."""

    url = f"{API_PREFIX}/glance/cards"
    name = f"{DOMAIN}:glance_cards"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        if not _glasses_pairing(request, hass):
            return self.json_message("invalid token", status_code=401)
        return self.json({"cards": _store(hass).cards})


class GlanceStatesView(HomeAssistantView):
    """States for the entities that appear on cards. Anything not on a card
    is unreachable through this endpoint — the glasses token cannot enumerate
    the rest of HA."""

    url = f"{API_PREFIX}/glance/states"
    name = f"{DOMAIN}:glance_states"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        if not _glasses_pairing(request, hass):
            return self.json_message("invalid token", status_code=401)
        wanted = _card_entity_ids(_store(hass).cards)
        out = []
        for eid in wanted:
            s = hass.states.get(eid)
            if s:
                out.append(s.as_dict())
        return self.json(out)


class GlanceCallServiceView(HomeAssistantView):
    """Scoped service-call proxy. Only services + targets present on a card
    are accepted. Implements both the implicit toggle (when the glasses tap
    an entity cell) and explicit action items."""

    url = f"{API_PREFIX}/glance/call_service"
    name = f"{DOMAIN}:glance_call_service"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        if not _glasses_pairing(request, hass):
            return self.json_message("invalid token", status_code=401)
        body: dict[str, Any] = await request.json()
        domain = body.get("domain")
        service = body.get("service")
        target = body.get("target") or {}
        target_eid = target.get("entity_id") if isinstance(target, dict) else None
        if not _service_call_allowed(_store(hass).cards, domain, service, target_eid):
            return self.json_message("service call not permitted by card config", status_code=403)
        try:
            await hass.services.async_call(
                domain=domain,
                service=service,
                target={"entity_id": target_eid} if target_eid else {},
                blocking=False,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("glance call_service failed")
            return self.json_message(f"call_service failed: {err}", status_code=500)
        return self.json({"ok": True})


class GlanceWebSocketView(HomeAssistantView):
    """Real-time state stream for the glasses.

    Protocol (browser WebSocket API can't set headers, so auth rides on the
    first message — same shape HA's own websocket uses):

        →  {"type": "auth", "access_token": "..."}
        ←  {"type": "auth_ok"} | {"type": "auth_invalid"}

        ←  {"type": "state_changed", "entity_id": ..., "new_state": {...}}
        ←  {"type": "state_changed", "entity_id": ..., "new_state": null}   # removed
        ←  {"type": "ping"}                                                  # keepalive

    Only state_changed events for entity_ids on cards are forwarded.
    """

    url = f"{API_PREFIX}/glance/ws"
    name = f"{DOMAIN}:glance_ws"
    requires_auth = False

    async def get(self, request: web.Request) -> web.WebSocketResponse:
        hass: HomeAssistant = request.app["hass"]
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        # First message is auth.
        try:
            first = await ws.receive(timeout=10)
        except Exception:  # noqa: BLE001
            await ws.close()
            return ws
        if first.type != web.WSMsgType.TEXT:
            await ws.close()
            return ws
        try:
            msg = json.loads(first.data)
        except (json.JSONDecodeError, ValueError):
            await ws.send_json({"type": "auth_invalid"})
            await ws.close()
            return ws
        token = msg.get("access_token") or msg.get("token")
        pairing = _store(hass).find_pairing_by_token(token) if token else None
        if not pairing:
            await ws.send_json({"type": "auth_invalid"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok"})

        @callback
        def state_listener(event):
            eid = event.data.get("entity_id")
            wanted = _card_entity_ids(_store(hass).cards)
            if eid not in wanted:
                return
            new_state = event.data.get("new_state")
            payload = {
                "type": "state_changed",
                "entity_id": eid,
                "new_state": new_state.as_dict() if new_state else None,
            }
            hass.async_create_task(ws.send_json(payload))

        remove = hass.bus.async_listen("state_changed", state_listener)
        try:
            async for incoming in ws:
                if incoming.type == web.WSMsgType.ERROR:
                    break
                # We don't expect any messages after auth, but tolerate pings.
                if incoming.type == web.WSMsgType.TEXT and incoming.data == "ping":
                    await ws.send_str("pong")
        finally:
            remove()
        return ws


class AuditView(HomeAssistantView):
    """Latest audit log entries (newest first). Used by the panel to show
    recent approvals, revocations, and config changes."""

    url = f"{API_PREFIX}/audit"
    name = f"{DOMAIN}:audit"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        return self.json({"audit": _store(hass).audit})


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
    AuditView,
    # Glasses-token-authenticated proxies — replace the glasses' old direct
    # access to HA's native /api/* with scope-limited equivalents.
    GlanceCardsView,
    GlanceStatesView,
    GlanceCallServiceView,
    GlanceWebSocketView,
]
