"""Persistent storage for Smart Glasses.

State that survives restarts:

- **cards**: dashboard cards the panel manages.
- **pairings**: one record per glasses-app session. A pairing starts when
  the glasses call ``/api/smart_glasses/pair/start`` and gets a short code.
  An HA-logged-in user "approves" that code from the management panel; on
  approval we generate a 32-byte random session token, hash it, and store
  the *hash*. The plaintext token is briefly kept in ``token_pickup`` until
  the glasses fetch it through PairTokenView, after which it's wiped — so
  the only place the plaintext lives long-term is the glasses' localStorage.
- **audit**: ring buffer of recent panel actions, capped at AUDIT_CAP.

Each pairing dict:
    {
      "session_id":   str,                # opaque, glasses-known
      "code":         str,                # short human-typed code (e.g. "ABCDEF")
      "user_id":      str | None,         # HA user id that approved (None before)
      "token_hash":   str | None,         # sha256(token) — what we compare against
      "token_pickup": str | None,         # plaintext, wiped on first PairTokenView fetch
      "created_at":   float,              # epoch seconds
      "approved_at":  float | None,
    }
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)


def hash_token(token: str) -> str:
    """SHA-256 hex digest. Used both at approval (to store) and on every
    glasses-token authentication (to compare against the stored hash)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _redact(value: str | None, prefix: int = 8) -> str:
    """Truncated identifier safe for logs. Keeps enough to disambiguate
    entries while not handing a scraper a usable handle."""
    if not value:
        return "<none>"
    if len(value) <= prefix:
        return value
    return f"{value[:prefix]}…"


class SmartGlassesStore:
    # Cap on retained audit entries. Newest first; older entries are dropped.
    AUDIT_CAP = 200

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, Any] = {"cards": [], "pairings": {}, "audit": []}
        # In-memory reverse index: token_hash → session_id. Rebuilt from the
        # persisted pairings dict at load time; mutated by approve/delete.
        # Lets find_pairing_by_token be O(1) instead of O(N) over pairings,
        # which matters if a user accumulates several approved glasses.
        self._token_index: dict[str, str] = {}

    async def async_load(self) -> None:
        loaded = await self._store.async_load()
        if loaded:
            self._data["pairings"] = dict(loaded.get("pairings") or {})
            self._data["audit"] = list(loaded.get("audit") or [])
            if "cards" in loaded:
                self._data["cards"] = list(loaded["cards"])
            else:
                # Migrate old 'entities' to a single 'Main' card
                entities = loaded.get("entities") or []
                if entities:
                    self._data["cards"] = [
                        {
                            "id": "card_0",
                            "name": "Main",
                            "items": [{"type": "entity", "entity_id": e} for e in entities],
                        }
                    ]
                else:
                    self._data["cards"] = []
        await self._migrate_legacy_llat_pairings()
        self._rebuild_token_index()

    def _rebuild_token_index(self) -> None:
        """Populate ``_token_index`` from the current pairings dict. Called
        after load and after migration; cheap (one pass over a small dict)."""
        self._token_index.clear()
        for sid, p in self._data["pairings"].items():
            h = p.get("token_hash")
            if h:
                self._token_index[h] = sid

    async def _migrate_legacy_llat_pairings(self) -> None:
        """One-shot cleanup for installs that pre-date the hashed-token model.

        v0.5 and earlier minted an HA Long-Lived Access Token at approval and
        stored it on the pairing record. v0.6 moved to opaque session tokens
        validated by hash. Revoke the leftover LLAT refresh_tokens here so a
        glasses device still holding the old plaintext can't keep hitting HA.

        Each pairing is migrated independently — a single bad record can't
        poison the rest of the dataset — and progress is persisted after
        every successful step so a crash mid-migration doesn't repeat work
        we've already done on the next start.
        """
        legacy = [
            (sid, p)
            for sid, p in self._data["pairings"].items()
            if "refresh_id" in p or "token" in p
        ]
        if not legacy:
            return
        _LOGGER.warning(
            "smart_glasses: migrating %d legacy LLAT pairing(s) — devices must re-pair",
            len(legacy),
        )
        migrated = 0
        for sid, p in legacy:
            try:
                refresh_id = p.get("refresh_id")
                if refresh_id:
                    try:
                        refresh = await self._hass.auth.async_get_refresh_token(refresh_id)
                        if refresh is not None:
                            await self._hass.auth.async_remove_refresh_token(refresh)
                    except Exception:  # noqa: BLE001
                        # Token may have been revoked externally already, or
                        # the auth manager doesn't recognize it. Either way,
                        # we proceed with deleting the local record below.
                        _LOGGER.exception(
                            "could not revoke legacy refresh token %s",
                            _redact(refresh_id),
                        )
                self._data["pairings"].pop(sid, None)
                try:
                    await self.async_save()
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "could not persist migration progress for pairing %s — "
                        "will retry on next start",
                        _redact(sid),
                    )
                migrated += 1
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "unexpected error migrating legacy pairing %s — skipping",
                    _redact(sid),
                )
        _LOGGER.info(
            "smart_glasses: migrated %d/%d legacy pairing(s)",
            migrated,
            len(legacy),
        )

    async def async_save(self) -> None:
        await self._store.async_save(self._data)

    # ---- cards --------------------------------------------------------

    @property
    def cards(self) -> list[dict[str, Any]]:
        return list(self._data["cards"])

    async def async_set_cards(self, cards: list[dict[str, Any]]) -> None:
        self._data["cards"] = cards
        await self.async_save()

    # ---- pairings --------------------------------------------------------

    @property
    def pairings(self) -> dict[str, dict[str, Any]]:
        return dict(self._data["pairings"])

    def get_pairing(self, session_id: str) -> dict[str, Any] | None:
        return self._data["pairings"].get(session_id)

    def find_pairing_by_code(self, code: str) -> dict[str, Any] | None:
        for p in self._data["pairings"].values():
            if p.get("code") == code:
                return p
        return None

    async def async_create_pairing(self, session_id: str, code: str) -> dict[str, Any]:
        pairing = {
            "session_id": session_id,
            "code": code,
            "user_id": None,
            "token_hash": None,
            "token_pickup": None,
            "created_at": time.time(),
            "approved_at": None,
        }
        self._data["pairings"][session_id] = pairing
        await self.async_save()
        return pairing

    async def async_approve_pairing(
        self,
        session_id: str,
        user_id: str,
        token: str,
    ) -> dict[str, Any]:
        """Mark a pairing approved. ``token`` is the freshly-minted plaintext
        session token; we store its hash and keep the plaintext in
        ``token_pickup`` so the glasses can fetch it on their next poll."""
        p = self._data["pairings"].get(session_id)
        if not p:
            raise KeyError(session_id)
        h = hash_token(token)
        p["user_id"] = user_id
        p["token_hash"] = h
        p["token_pickup"] = token
        p["approved_at"] = time.time()
        self._token_index[h] = session_id
        await self.async_save()
        return p

    async def async_clear_pickup(self, session_id: str) -> None:
        """Wipe the plaintext pickup token after the glasses have fetched it.
        The hash stays for ongoing auth; the plaintext can't be retrieved
        from storage anymore."""
        p = self._data["pairings"].get(session_id)
        if p and p.get("token_pickup"):
            p["token_pickup"] = None
            await self.async_save()

    def find_pairing_by_token(self, token: str) -> dict[str, Any] | None:
        """Look up a pairing by Bearer token — used on every glasses-API call.

        O(1) average via the ``_token_index`` reverse map. The final equality
        check (after the dict hit) goes through ``hmac.compare_digest`` so a
        timing attacker can't probe matching prefixes of the stored hash.
        """
        if not token:
            return None
        h = hash_token(token)
        sid = self._token_index.get(h)
        if not sid:
            return None
        p = self._data["pairings"].get(sid)
        if not p:
            return None
        stored = p.get("token_hash")
        if not stored or not hmac.compare_digest(stored, h):
            return None
        return p

    async def async_delete_pairing(self, session_id: str) -> dict[str, Any] | None:
        p = self._data["pairings"].pop(session_id, None)
        if p is not None:
            h = p.get("token_hash")
            if h:
                self._token_index.pop(h, None)
            await self.async_save()
        return p

    # ---- audit log -------------------------------------------------------

    @property
    def audit(self) -> list[dict[str, Any]]:
        return list(self._data["audit"])

    async def async_audit(self, action: str, **fields: Any) -> None:
        """Append an audit entry. Capped at AUDIT_CAP (oldest dropped).

        Tokens / refresh_ids must NEVER be logged here — anything stored
        ends up serialised to .storage/smart_glasses alongside the data.
        """
        entry: dict[str, Any] = {"ts": time.time(), "action": action}
        entry.update({k: v for k, v in fields.items() if k not in {"token", "refresh_id"}})
        self._data["audit"].insert(0, entry)
        if len(self._data["audit"]) > self.AUDIT_CAP:
            self._data["audit"] = self._data["audit"][: self.AUDIT_CAP]
        await self.async_save()
