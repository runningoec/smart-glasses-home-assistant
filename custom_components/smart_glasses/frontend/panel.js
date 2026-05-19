// Smart Glasses — HA management panel.
//
// Lives at <ha>/smart-glasses inside Home Assistant's frontend. Two cards:
//   1. Entity picker — search HA states, pick up to MAX_ENTITIES.
//   2. Pairings — see pending pairing codes, approve them, revoke approved.
//
// Plain custom element with no build step so it can ship via HACS as-is.

const MAX_ENTITIES = 8;

class SmartGlassesPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._entities = [];      // currently-selected entity_ids
    this._pairings = [];      // server-reported pairings
    this._search = "";        // entity picker search box
    this._approveCode = "";   // pairings approve-code input
    this._dirtyEntities = false;
    this._loaded = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) {
      this._loaded = true;
      this._loadAll();
    }
    // We deliberately DON'T re-render on every hass update. HA's frontend
    // pushes a new hass object on every state change anywhere in the system
    // — re-rendering on each one tore through input focus and scroll
    // position. The management panel doesn't need live state; the user
    // sees a snapshot when they open it, and explicit actions trigger
    // their own re-renders.
  }
  set narrow(narrow) { this._narrow = narrow; }
  set route(route) { this._route = route; }
  set panel(panel) { this._panel = panel; }

  connectedCallback() {
    this._render();
  }

  // ---- networking ---------------------------------------------------------

  get _authToken() {
    return this._hass?.auth?.data?.access_token
        ?? this._hass?.connection?.options?.auth?.data?.access_token;
  }

  async _api(method, path, body) {
    const headers = { "content-type": "application/json" };
    const token = this._authToken;
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`/api/smart_glasses${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const txt = await res.text();
      throw new Error(`${method} ${path} ${res.status}: ${txt}`);
    }
    return res.status === 204 ? null : res.json();
  }

  async _loadAll() {
    try {
      const [eRes, pRes] = await Promise.all([
        this._api("GET", "/entities"),
        this._api("GET", "/pairings"),
      ]);
      this._entities = eRes.entities ?? [];
      this._pairings = pRes.pairings ?? [];
      this._render();
    } catch (err) {
      console.error("smart_glasses load failed", err);
      this._error = err.message;
      this._render();
    }
  }

  async _saveEntities() {
    try {
      await this._api("PUT", "/entities", { entities: this._entities });
      this._dirtyEntities = false;
      this._error = null;
    } catch (err) {
      this._error = err.message;
    }
    this._render();
  }

  async _approve(code) {
    try {
      await this._api("POST", "/pair/approve", { code });
      this._approveCode = "";
      await this._loadAll();
    } catch (err) {
      this._error = err.message;
      this._render();
    }
  }

  async _revoke(sessionId) {
    try {
      await this._api("DELETE", `/pair/${sessionId}`);
      await this._loadAll();
    } catch (err) {
      this._error = err.message;
      this._render();
    }
  }

  // ---- rendering ----------------------------------------------------------

  _allEntities() {
    if (!this._hass) return [];
    return Object.values(this._hass.states).sort((a, b) =>
      (a.attributes.friendly_name || a.entity_id).localeCompare(
        b.attributes.friendly_name || b.entity_id
      )
    );
  }

  _toggle(entityId) {
    const idx = this._entities.indexOf(entityId);
    if (idx >= 0) {
      this._entities.splice(idx, 1);
    } else {
      if (this._entities.length >= MAX_ENTITIES) return;
      this._entities.push(entityId);
    }
    this._dirtyEntities = true;
    this._render();
  }

  _render() {
    if (!this._hass) {
      this.innerHTML = "<p style='padding:24px'>Loading…</p>";
      return;
    }

    // Snapshot what we want to preserve across the upcoming innerHTML wipe.
    // We re-render rarely (only on explicit user actions) so this is cheap.
    const active = document.activeElement;
    const focusedAction = active?.closest?.("[data-action]")?.dataset?.action;
    const selStart = (active && "selectionStart" in active) ? active.selectionStart : null;
    const selEnd   = (active && "selectionEnd"   in active) ? active.selectionEnd   : null;
    const listScroll = this.querySelector(".entity-list")?.scrollTop ?? 0;

    const search = this._search.toLowerCase();
    const matching = this._allEntities()
      .filter((s) => {
        if (!search) return true;
        return (
          s.entity_id.toLowerCase().includes(search) ||
          (s.attributes.friendly_name || "").toLowerCase().includes(search)
        );
      })
      .slice(0, 80);

    const selectedSet = new Set(this._entities);

    this.innerHTML = `
      <style>
        :host, .root { color: var(--primary-text-color); }
        .root { max-width: 960px; margin: 0 auto; padding: 24px; }
        .card {
          background: var(--card-background-color, #1c1c1c);
          border-radius: 12px; padding: 20px; margin-bottom: 20px;
          box-shadow: var(--ha-card-box-shadow, 0 2px 4px rgba(0,0,0,0.2));
        }
        h2 { margin: 0 0 8px; font-size: 20px; }
        .meta { color: var(--secondary-text-color); font-size: 14px; margin-bottom: 16px; }
        input[type="text"] {
          width: 100%; padding: 10px 12px; font-size: 16px; box-sizing: border-box;
          background: var(--input-fill-color, #2a2a2a);
          color: var(--primary-text-color); border: 1px solid var(--divider-color, #444);
          border-radius: 8px;
        }
        .entity-list {
          max-height: 360px; overflow-y: auto; margin-top: 12px;
          border: 1px solid var(--divider-color, #444); border-radius: 8px;
        }
        .entity {
          display: flex; align-items: center; justify-content: space-between;
          padding: 10px 14px; border-bottom: 1px solid var(--divider-color, #333);
          cursor: pointer;
        }
        .entity:hover { background: var(--secondary-background-color, #2a2a2a); }
        .entity:last-child { border-bottom: none; }
        .entity.selected { background: rgba(100, 200, 255, 0.12); }
        .entity-name { flex: 1; }
        .entity-id { color: var(--secondary-text-color); font-size: 12px; }
        .selected-row {
          display: flex; align-items: center; justify-content: space-between;
          padding: 10px 14px; background: var(--secondary-background-color, #2a2a2a);
          border-radius: 6px; margin-bottom: 6px;
        }
        button {
          background: var(--primary-color, #03a9f4); color: white; border: 0;
          padding: 10px 18px; border-radius: 8px; font-size: 14px; cursor: pointer;
        }
        button.danger { background: var(--error-color, #ef5350); }
        button.secondary { background: transparent; color: var(--primary-color, #03a9f4); }
        button[disabled] { opacity: 0.5; cursor: not-allowed; }
        .pair-row {
          display: flex; align-items: center; justify-content: space-between;
          padding: 12px 0; border-bottom: 1px solid var(--divider-color, #333);
        }
        .pair-row:last-child { border-bottom: none; }
        .pair-code { font-family: ui-monospace, monospace; font-size: 22px; font-weight: 700; letter-spacing: 2px; }
        .error { color: var(--error-color, #ef5350); margin-top: 12px; font-size: 14px; }
        .pill { display: inline-block; padding: 3px 8px; border-radius: 10px;
                font-size: 12px; background: rgba(255,255,255,0.08); margin-left: 8px; }
        .approve-row { display: flex; gap: 8px; margin-top: 12px; }
        .approve-row input { flex: 1; }
      </style>
      <div class="root">
        ${this._error ? `<div class="error">${this._error}</div>` : ""}

        <div class="card">
          <h2>Selected entities (${this._entities.length}/${MAX_ENTITIES})</h2>
          <div class="meta">These are what the glasses display. Click an entity below to add/remove.</div>
          ${this._entities.length === 0
            ? `<div class="meta">No entities selected yet.</div>`
            : this._entities.map((eid) => {
                const s = this._hass.states[eid];
                const name = s?.attributes.friendly_name || eid;
                const state = s ? `${s.state}${s.attributes.unit_of_measurement ? " " + s.attributes.unit_of_measurement : ""}` : "—";
                return `
                  <div class="selected-row">
                    <div>
                      <div class="entity-name">${name}</div>
                      <div class="entity-id">${eid} · <span style="color:var(--primary-text-color)">${state}</span></div>
                    </div>
                    <button class="secondary" data-action="remove" data-entity="${eid}">Remove</button>
                  </div>
                `;
              }).join("")
          }
          <div style="margin-top: 16px;">
            <button data-action="save" ${this._dirtyEntities ? "" : "disabled"}>Save</button>
          </div>
        </div>

        <div class="card">
          <h2>Pick entities</h2>
          <input type="text" placeholder="Search by name or entity_id…" data-action="search" value="${this._search}">
          <div class="entity-list">
            ${matching.length === 0
              ? `<div class="entity">No matches.</div>`
              : matching.map((s) => `
                  <div class="entity ${selectedSet.has(s.entity_id) ? "selected" : ""}" data-action="toggle" data-entity="${s.entity_id}">
                    <div>
                      <div class="entity-name">${s.attributes.friendly_name || s.entity_id}</div>
                      <div class="entity-id">${s.entity_id} · ${s.state}</div>
                    </div>
                    <div>${selectedSet.has(s.entity_id) ? "✓" : ""}</div>
                  </div>
                `).join("")
            }
          </div>
        </div>

        <div class="card">
          <h2>Glasses pairings</h2>
          <div class="meta">When the glasses load the Web App, they show a short code. Type it here to approve. The glasses then poll for the access token.</div>

          <div class="approve-row">
            <input type="text" placeholder="ABCDEF" data-action="code" value="${this._approveCode}" maxlength="8" style="text-transform: uppercase; font-family: monospace; font-size: 18px; letter-spacing: 4px;">
            <button data-action="approve">Approve</button>
          </div>

          <div style="margin-top: 16px;">
            ${this._pairings.length === 0
              ? `<div class="meta">No pairings yet.</div>`
              : this._pairings.map((p) => `
                  <div class="pair-row">
                    <div>
                      <span class="pair-code">${p.code}</span>
                      <span class="pill">${p.approved ? "approved" : "pending"}</span>
                    </div>
                    <button class="danger" data-action="revoke" data-session="${p.session_id}">Revoke</button>
                  </div>
                `).join("")
            }
          </div>
        </div>
      </div>
    `;

    // Restore focus + scroll. Inputs identified by data-action.
    if (focusedAction) {
      const target = this.querySelector(`[data-action="${focusedAction}"]`);
      if (target && typeof target.focus === "function") {
        target.focus();
        if (selStart !== null && "setSelectionRange" in target) {
          try { target.setSelectionRange(selStart, selEnd ?? selStart); } catch { /* ignore */ }
        }
      }
    }
    const list = this.querySelector(".entity-list");
    if (list) list.scrollTop = listScroll;

    // Wire up event handlers (delegated).
    this.querySelectorAll("[data-action]").forEach((el) => {
      el.addEventListener(el.tagName === "INPUT" ? "input" : "click", (evt) => {
        const action = el.dataset.action;
        if (action === "toggle") {
          this._toggle(el.dataset.entity);
        } else if (action === "remove") {
          this._toggle(el.dataset.entity);
        } else if (action === "save") {
          this._saveEntities();
        } else if (action === "search") {
          this._search = el.value;
          // Re-render lazily to avoid losing focus on input — small debounce.
          clearTimeout(this._searchTimer);
          this._searchTimer = setTimeout(() => this._render(), 100);
        } else if (action === "code") {
          this._approveCode = el.value.toUpperCase();
        } else if (action === "approve") {
          if (this._approveCode) this._approve(this._approveCode);
        } else if (action === "revoke") {
          if (confirm("Revoke this pairing? The glasses will lose access.")) {
            this._revoke(el.dataset.session);
          }
        }
      });
    });
  }
}

customElements.define("smart-glasses-panel", SmartGlassesPanel);
