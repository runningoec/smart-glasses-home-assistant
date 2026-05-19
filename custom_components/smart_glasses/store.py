"""Persistent storage for Smart Glasses.

Two pieces of state survive restarts:

- **entities**: the list of entity_ids the user selected in the management
  panel. The glasses Web App renders these as its adaptive grid.
- **pairings**: one record per glasses-app session. A pairing starts when
  the glasses call ``/api/smart_glasses/pair/start`` and gets a short code.
  An HA-logged-in user can then "approve" that code from the management
  panel; on approval we mint a Long-Lived Access Token (LLAT) owned by the
  approving user and store its refresh-token id so it can be revoked later.

Each pairing dict:
    {
      "session_id":  str,                # opaque, glasses-known
      "code":        str,                # short human-typed code (e.g. "ABCDEF")
      "user_id":     str | None,         # HA user id that approved (None before)
      "refresh_id": str | None,          # HA refresh-token id, for revocation
      "token":       str | None,         # the LLAT itself (the glasses fetch this)
      "created_at":  float,              # epoch seconds
      "approved_at": float | None,
    }
"""

from __future__ import annotations

import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION


class SmartGlassesStore:
    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, Any] = {"entities": [], "pairings": {}}

    async def async_load(self) -> None:
        loaded = await self._store.async_load()
        if loaded:
            # Merge so newly-added fields (in later versions) get defaults.
            self._data["entities"] = list(loaded.get("entities") or [])
            self._data["pairings"] = dict(loaded.get("pairings") or {})

    async def async_save(self) -> None:
        await self._store.async_save(self._data)

    # ---- entities --------------------------------------------------------

    @property
    def entities(self) -> list[str]:
        return list(self._data["entities"])

    async def async_set_entities(self, entities: list[str]) -> None:
        self._data["entities"] = list(entities)
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
            "refresh_id": None,
            "token": None,
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
        refresh_id: str,
        token: str,
    ) -> dict[str, Any]:
        p = self._data["pairings"].get(session_id)
        if not p:
            raise KeyError(session_id)
        p["user_id"] = user_id
        p["refresh_id"] = refresh_id
        p["token"] = token
        p["approved_at"] = time.time()
        await self.async_save()
        return p

    async def async_delete_pairing(self, session_id: str) -> dict[str, Any] | None:
        p = self._data["pairings"].pop(session_id, None)
        if p is not None:
            await self.async_save()
        return p
