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
    this._lastRenderKey = null;       // cheap content hash; skip render if unchanged
    this._renderCount = 0;
    this._connectCount = 0;
    console.log("[smart_glasses] panel constructor");
  }

  set hass(hass) {
    if (!hass) return;       // ignore null/undefined setter calls
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
    this._connectCount++;
    console.log("[smart_glasses] connectedCallback #" + this._connectCount);
    this._render();
  }

  disconnectedCallback() {
    console.log("[smart_glasses] disconnectedCallback");
  }

  // ---- networking ---------------------------------------------------------

  async _api(method, path, body) {
    const trimmed = path.startsWith("/") ? path.slice(1) : path;

    // Prefer hass.callApi — it's HA's own frontend → backend helper. It
    // attaches auth the same way HA's built-in calls do, which is the
    // reliable cross-version path. Manual `fetch(... Bearer)` worked for
    // GETs (session cookie carried us) but failed on POST/PUT/DELETE
    // because the cookie isn't accepted there.
    if (typeof this._hass?.callApi === "function") {
      try {
        return await this._hass.callApi(method, `smart_glasses/${trimmed}`, body);
      } catch (err) {
        const status = err?.status_code ?? err?.code ?? "?";
        const bodyMsg = (typeof err?.body === "string")
          ? err.body
          : (err?.body?.message ?? err?.body?.error ?? JSON.stringify(err?.body ?? err?.message ?? ""));
        throw new Error(`${method} ${path} ${status}: ${bodyMsg}`);
      }
    }

    // Fallback for any frontend that doesn't expose callApi.
    const headers = { "content-type": "application/json" };
    const token = this._hass?.auth?.accessToken
              ?? this._hass?.auth?.data?.access_token
              ?? this._hass?.connection?.options?.auth?.accessToken
              ?? this._hass?.connection?.options?.auth?.data?.access_token;
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`/api/smart_glasses${path}`, {
      method,
      headers,
      credentials: "same-origin",
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
    this._renderCount++;
    if (!this._hass) {
      this.innerHTML = "<p style='padding:24px'>Loading…</p>";
      return;
    }

    // De-dup: skip the DOM rewrite if the inputs to this render are
    // unchanged from last time. This is the belt to our suspenders — even
    // if HA's frontend invokes setters or remounts the panel in a tight
    // loop, we won't actually rewrite innerHTML unless something changed.
    const key = JSON.stringify({
      e: this._entities,
      p: this._pairings,
      s: this._search,
      c: this._approveCode,
      d: this._dirtyEntities,
      err: this._error ?? null,
    });
    if (key === this._lastRenderKey && this.children.length > 0) {
      return;
    }
    this._lastRenderKey = key;
    if (this._renderCount <= 10 || this._renderCount % 25 === 0) {
      console.log("[smart_glasses] _render #" + this._renderCount + " (dom-rewrite)");
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

    // Suggest the public Web App URL we'd register with Meta. If the user is
    // viewing the panel from localhost/HTTP we can't recommend their current
    // origin (Meta requires HTTPS reachable from the open internet), so we
    // fall back to a placeholder they have to replace.
    const origin = location.origin;
    const originLooksPublic = location.protocol === "https:" &&
      !/^(localhost|127\.|10\.|192\.168\.|0\.0\.0\.0|\[?::1\]?)/.test(location.hostname);
    const webAppUrl = originLooksPublic
      ? `${origin}/smart-glasses-app`
      : `https://<your-ha-public-domain>/smart-glasses-app`;
    const setupOpen = this._pairings.length === 0 ? "open" : "";

    this.innerHTML = `
      <style>
        :host, .root { color: var(--primary-text-color); }
        .root { max-width: 960px; margin: 0 auto; padding: 24px; }
        .card {
          background: var(--card-background-color, #1c1c1c);
          border-radius: 12px; padding: 20px; margin-bottom: 20px;
          box-shadow: var(--ha-card-box-shadow, 0 2px 4px rgba(0,0,0,0.2));
          transition: transform 0.2s ease, box-shadow 0.2s ease;
        }
        .card:hover {
          transform: translateY(-2px);
          box-shadow: 0 6px 12px rgba(0,0,0,0.3);
        }
        h2 { margin: 0 0 8px; font-size: 20px; }
        .meta { color: var(--secondary-text-color); font-size: 14px; margin-bottom: 16px; }
        input[type="text"] {
          width: 100%; padding: 10px 12px; font-size: 16px; box-sizing: border-box;
          background: var(--input-fill-color, #2a2a2a);
          color: var(--primary-text-color); border: 1px solid var(--divider-color, #444);
          border-radius: 8px; transition: border-color 0.2s, box-shadow 0.2s;
        }
        input[type="text"]:focus {
          outline: none; border-color: var(--primary-color, #03a9f4);
          box-shadow: 0 0 0 2px rgba(3, 169, 244, 0.2);
        }
        details.setup > summary {
          cursor: pointer; list-style: none; font-size: 20px; font-weight: 600;
          padding: 0 0 4px;
          display: flex; align-items: center; justify-content: space-between;
          transition: color 0.2s;
        }
        details.setup > summary:hover { color: var(--primary-color, #03a9f4); }
        details.setup > summary::after { content: "▾"; font-size: 16px; color: var(--secondary-text-color); }
        details.setup[open] > summary::after { content: "▴"; }
        details.setup > summary::-webkit-details-marker { display: none; }
        ol.steps { padding-left: 22px; margin: 12px 0 0; }
        ol.steps li { margin: 8px 0; line-height: 1.5; }
        ol.steps code { background: var(--secondary-background-color, #2a2a2a); padding: 2px 6px; border-radius: 4px; }
        .url-box {
          display: flex; align-items: stretch; gap: 8px; margin: 12px 0;
        }
        .url-box code {
          flex: 1; padding: 12px 14px; font-family: ui-monospace, "SF Mono", monospace;
          background: var(--secondary-background-color, #2a2a2a); border-radius: 8px;
          overflow-x: auto; white-space: nowrap; user-select: all;
          transition: background 0.2s;
        }
        .url-box code:hover { background: var(--hover-background-color, #333); }
        .url-box.placeholder code { color: var(--warning-color, #fc6); }
        .copy-toast {
          display: inline-block; margin-left: 8px; font-size: 12px;
          color: var(--success-color, #4caf50); opacity: 0; transition: opacity 0.2s;
        }
        .copy-toast.visible { opacity: 1; }
        .entity-list {
          max-height: 360px; overflow-y: auto; margin-top: 12px;
          border: 1px solid var(--divider-color, #444); border-radius: 8px;
        }
        .entity {
          display: flex; align-items: center; justify-content: space-between;
          padding: 10px 14px; border-bottom: 1px solid var(--divider-color, #333);
          cursor: pointer; transition: background 0.15s ease;
        }
        .entity:hover { background: var(--secondary-background-color, #2a2a2a); }
        .entity:active { background: rgba(100, 200, 255, 0.05); }
        .entity:last-child { border-bottom: none; }
        .entity.selected { background: rgba(100, 200, 255, 0.12); }
        .entity-name { flex: 1; }
        .entity-id { color: var(--secondary-text-color); font-size: 12px; }
        .selected-row {
          display: flex; align-items: center; justify-content: space-between;
          padding: 10px 14px; background: var(--secondary-background-color, #2a2a2a);
          border-radius: 6px; margin-bottom: 6px; transition: transform 0.2s ease, box-shadow 0.2s ease;
        }
        .selected-row:hover {
          transform: translateX(4px);
          box-shadow: -2px 2px 4px rgba(0,0,0,0.1);
        }
        button {
          background: var(--primary-color, #03a9f4); color: white; border: 0;
          padding: 10px 18px; border-radius: 8px; font-size: 14px; cursor: pointer;
          transition: filter 0.2s, transform 0.1s, background 0.2s;
        }
        button:hover:not([disabled]) { filter: brightness(1.1); transform: scale(1.02); }
        button:active:not([disabled]) { transform: scale(0.98); }
        button.danger { background: var(--error-color, #ef5350); }
        button.secondary { background: transparent; color: var(--primary-color, #03a9f4); }
        button.secondary:hover:not([disabled]) { background: rgba(3, 169, 244, 0.1); }
        button[disabled] { opacity: 0.5; cursor: not-allowed; transform: none; filter: none; }
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
          <details class="setup" ${setupOpen}>
            <summary>Add to your glasses</summary>
            <div class="meta" style="margin-top:8px">
              Requires <strong>Meta AI app v272+</strong> and glasses firmware
              <strong>v125+</strong>. Your HA must be reachable on HTTPS from the
              open internet (Nabu Casa, Cloudflare Tunnel, or your own reverse proxy).
            </div>

            <div style="font-weight:600; margin-top:14px;">Web App URL</div>
            <div class="url-box ${originLooksPublic ? "" : "placeholder"}">
              <code data-action="webapp-url">${webAppUrl}</code>
              <button data-action="copy-webapp-url">Copy</button>
              <span class="copy-toast" data-copy-toast>Copied</span>
            </div>
            ${originLooksPublic
              ? `<div class="meta">Auto-filled from your current address. If your glasses pair from outside the LAN, use that address instead.</div>`
              : `<div class="meta">Replace <code>&lt;your-ha-public-domain&gt;</code> with the address you use to reach HA from the internet.</div>`}

            <ol class="steps">
              <li><strong>Enable Developer Mode in Meta AI</strong> (one-time):
                  Meta AI app → Settings → App Info → tap the app version number
                  <strong>5 times</strong> in a row → confirm.</li>
              <li><strong>Add the Web App</strong>:
                  Meta AI app → App Settings → App Connections → Web Apps →
                  <strong>Add a Web App</strong> → name it (e.g. <code>HA Glasses</code>)
                  → paste the URL above → <strong>Connect</strong>.</li>
              <li><strong>Launch on the glasses</strong>: the new app appears at
                  the bottom of your app grid. Open it.</li>
              <li><strong>Pair</strong>: the glasses show a 6-character code →
                  type it in the <em>Glasses pairings</em> card below →
                  <strong>Approve</strong>.</li>
            </ol>
          </details>
        </div>

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
          const search = this._search.toLowerCase();
          const matching = this._allEntities().filter((s) => {
            if (!search) return true;
            return (s.entity_id.toLowerCase().includes(search) || (s.attributes.friendly_name || "").toLowerCase().includes(search));
          }).slice(0, 80);
          
          const list = this.querySelector(".entity-list");
          if (list) {
            const selectedSet = new Set(this._entities);
            list.innerHTML = matching.length === 0 ? `<div class="entity">No matches.</div>` : matching.map((s) => `
                  <div class="entity ${selectedSet.has(s.entity_id) ? "selected" : ""}" data-action="toggle" data-entity="${s.entity_id}">
                    <div>
                      <div class="entity-name">${s.attributes.friendly_name || s.entity_id}</div>
                      <div class="entity-id">${s.entity_id} · ${s.state}</div>
                    </div>
                    <div>${selectedSet.has(s.entity_id) ? "✓" : ""}</div>
                  </div>
                `).join("");
            
            list.querySelectorAll("[data-action]").forEach((elNode) => {
              elNode.addEventListener("click", () => {
                if (elNode.dataset.action === "toggle") this._toggle(elNode.dataset.entity);
              });
            });
          }
        } else if (action === "code") {
          this._approveCode = el.value.toUpperCase();
        } else if (action === "approve") {
          if (this._approveCode) this._approve(this._approveCode);
        } else if (action === "revoke") {
          if (confirm("Revoke this pairing? The glasses will lose access.")) {
            this._revoke(el.dataset.session);
          }
        } else if (action === "copy-webapp-url") {
          const url = this.querySelector('[data-action="webapp-url"]')?.textContent ?? "";
          this._copyToClipboard(url);
        }
      });
    });
  }

  async _copyToClipboard(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Fallback for browsers that block writeText (e.g. on insecure origins).
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch { /* give up */ }
      document.body.removeChild(ta);
    }
    const toast = this.querySelector("[data-copy-toast]");
    if (toast) {
      toast.classList.add("visible");
      clearTimeout(this._copyToastTimer);
      this._copyToastTimer = setTimeout(() => toast.classList.remove("visible"), 1200);
    }
  }
}

customElements.define("smart-glasses-panel", SmartGlassesPanel);
