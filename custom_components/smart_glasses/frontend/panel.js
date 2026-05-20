// Smart Glasses — HA management panel.
//
// Lives at <ha>/smart-glasses inside Home Assistant's frontend. Two cards:
//   1. Entity picker — search HA states, pick up to MAX_ENTITIES.
//   2. Pairings — see pending pairing codes, approve them, revoke approved.
//
// Plain custom element with no build step so it can ship via HACS as-is.

const MAX_ENTITIES = 8;

// HTML-escape any user-supplied string before interpolating into innerHTML.
// Without this, an entity with friendly_name "<img src=x onerror=alert(1)>"
// would execute the script in this panel's context. (Not exploitable without
// HA admin in the first place, but no reason to leave it open.)
const esc = (s) => String(s ?? "")
  .replace(/&/g, "&amp;")
  .replace(/</g, "&lt;")
  .replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;")
  .replace(/'/g, "&#39;");

function timeAgo(ts) {
  if (!ts) return "";
  const sec = Math.max(0, (Date.now() / 1000) - ts);
  if (sec < 60) return `${Math.floor(sec)}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

function describeAudit(a) {
  const who = a.user_name ? `<strong>${esc(a.user_name)}</strong>` : "someone";
  switch (a.action) {
    case "pair_approved": return `${who} approved pairing <code>${esc(a.code)}</code>`;
    case "pair_revoked":  return `${who} revoked pairing <code>${esc(a.code ?? "?")}</code>${a.had_token ? " (had token)" : ""}`;
    case "cards_saved":   return `${who} saved <strong>${a.card_count}</strong> cards (${a.total_items} items, via ${esc(a.source ?? "?")})`;
    default:              return `${who} ${esc(a.action)}`;
  }
}

// Describe the confirm config in human terms for the readonly indicator
// shown on items whose confirm is a time window (the checkbox can't express
// time bounds — those have to be set via the YAML editor).
function describeConfirm(c) {
  if (c === true) return "always";
  if (!c || typeof c !== "object") return "";
  const parts = [];
  if (c.after)  parts.push(`after ${c.after}`);
  if (c.before) parts.push(`before ${c.before}`);
  return parts.join(", ") || "always";
}

class SmartGlassesPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._cards = [];         // user's dashboard cards
    this._selectedCardId = null; // ID of the currently edited card
    this._pairings = [];      // server-reported pairings
    this._search = "";        // entity picker search box
    this._approveCode = "";   // pairings approve-code input
    this._yamlText = "";      // current cards rendered as YAML (server-formatted)
    this._yamlDirty = false;  // textarea diverges from _yamlText?
    this._yamlMessage = "";   // last apply/validation message
    this._audit = [];         // audit log entries, newest first
    this._toast = "";         // transient banner shown above the panel
    this._addTab = "entity";  // sub-tab in the Add panel — "entity" | "action"
    this._dirtyCards = false;
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
      const [cRes, pRes, yamlText, aRes] = await Promise.all([
        this._api("GET", "/cards"),
        this._api("GET", "/pairings"),
        this._yamlFetch().catch(() => ""), // non-fatal: panel still works without YAML
        this._api("GET", "/audit").catch(() => ({ audit: [] })),
      ]);
      this._cards = cRes.cards ?? [];
      if (this._cards.length > 0 && !this._selectedCardId) {
        this._selectedCardId = this._cards[0].id;
      }
      this._pairings = pRes.pairings ?? [];
      if (!this._yamlDirty) this._yamlText = yamlText;
      this._audit = aRes.audit ?? [];
      this._render();
    } catch (err) {
      console.error("smart_glasses load failed", err);
      this._error = err.message;
      this._render();
    }
  }

  _showToast(msg) {
    this._toast = msg;
    this._render();
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => { this._toast = ""; this._render(); }, 3000);
  }

  // YAML calls go through plain fetch because the endpoint returns/accepts
  // text/yaml, which hass.callApi doesn't handle. Auth uses whichever token
  // path hass exposes; same set of fallbacks as the manual fetch in _api.
  get _bearer() {
    return this._hass?.auth?.accessToken
        ?? this._hass?.auth?.data?.access_token
        ?? this._hass?.connection?.options?.auth?.accessToken
        ?? this._hass?.connection?.options?.auth?.data?.access_token;
  }

  async _yamlFetch() {
    const headers = {};
    const tok = this._bearer; if (tok) headers.Authorization = `Bearer ${tok}`;
    const res = await fetch("/api/smart_glasses/cards/yaml", {
      method: "GET", headers, credentials: "same-origin",
    });
    if (!res.ok) throw new Error(`yaml GET ${res.status}`);
    return res.text();
  }

  async _yamlApply() {
    const headers = { "content-type": "text/yaml" };
    const tok = this._bearer; if (tok) headers.Authorization = `Bearer ${tok}`;
    const ta = this.querySelector('[data-action="yaml-text"]');
    const text = ta?.value ?? "";
    try {
      const res = await fetch("/api/smart_glasses/cards/yaml", {
        method: "PUT", headers, credentials: "same-origin", body: text,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        this._yamlMessage = `Error: ${body.message ?? res.status}`;
      } else {
        this._yamlMessage = "Applied.";
        this._yamlDirty = false;
        await this._loadAll();
        return;
      }
    } catch (err) {
      this._yamlMessage = `Error: ${err.message}`;
    }
    this._render();
  }

  async _saveCards() {
    try {
      await this._api("PUT", "/cards", { cards: this._cards });
      this._dirtyCards = false;
      this._error = null;
      // Tell the user what just happened — admins won't always remember
      // that the glasses pick up changes on their next minute-poll.
      this._showToast("Saved · glasses pick up changes within a minute");
      await this._loadAll();
    } catch (err) {
      this._error = err.message;
      this._render();
    }
  }

  async _approve(code) {
    // Refresh pairings first. The cached list only updates on _loadAll, so
    // if the user launched the glasses app AFTER opening the panel, the new
    // pending pairing isn't in our cache yet — and we'd error out before
    // even hitting the server.
    try {
      const pRes = await this._api("GET", "/pairings");
      this._pairings = pRes.pairings ?? [];
    } catch (err) {
      // Refresh failed; fall through with whatever we have cached. The
      // server-side check still guards against bogus approvals.
      console.warn("pairings refresh before approve failed:", err);
    }

    // Bind approval to (code, session_id). The panel looks up the session_id
    // locally so the user only has to type the code. If two pending pairings
    // share a code (rare), the first row wins — user can revoke and retry.
    const match = this._pairings.find(p => !p.approved && p.code === code);
    if (!match) {
      this._error = `No pending pairing with code ${code}. Try clicking Revoke on any stale entries, or re-launch the glasses app for a fresh code.`;
      this._render();
      return;
    }
    try {
      await this._api("POST", "/pair/approve", {
        code,
        session_id: match.session_id,
      });
      this._approveCode = "";
      this._showToast(`Approved ${code}`);
      await this._loadAll();
    } catch (err) {
      this._error = err.message;
      this._render();
    }
  }

  async _revoke(sessionId) {
    try {
      await this._api("DELETE", `/pair/${sessionId}`);
      this._showToast("Revoked");
      await this._loadAll();
    } catch (err) {
      this._error = err.message;
      this._render();
    }
  }

  async _approveRow(code, sessionId) {
    // One-click approval direct from a pairing row. Skips the
    // type-the-code-and-look-it-up dance; we already have both bits.
    try {
      await this._api("POST", "/pair/approve", { code, session_id: sessionId });
      this._showToast(`Approved ${code}`);
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

  _toggleItem(entityId) {
    const card = this._cards.find(c => c.id === this._selectedCardId);
    if (!card) return;
    const idx = card.items.findIndex(i => i.type === 'entity' && i.entity_id === entityId);
    if (idx >= 0) {
      card.items.splice(idx, 1);
    } else {
      if (card.items.length >= MAX_ENTITIES) return;
      card.items.push({ type: 'entity', entity_id: entityId });
    }
    this._dirtyCards = true;
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
      c: this._cards,
      sc: this._selectedCardId,
      p: this._pairings,
      s: this._search,
      ac: this._approveCode,
      d: this._dirtyCards,
      err: this._error ?? null,
      yt: this._yamlText,
      ym: this._yamlMessage,
      au: this._audit,
      t: this._toast,
      at: this._addTab,
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

    const currentCard = this._cards.find(c => c.id === this._selectedCardId);

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
        /* Shared style for the panel's top-level collapsibles — setup help,
           YAML editor, audit log. Keeps the closed-box height and the
           chevron consistent across all three. */
        details.collapsible > summary {
          cursor: pointer; list-style: none; font-size: 20px; font-weight: 600;
          padding: 0;
          display: flex; align-items: center; justify-content: space-between;
          transition: color 0.2s;
        }
        details.collapsible > summary:hover { color: var(--primary-color, #03a9f4); }
        details.collapsible > summary::after {
          content: "▾"; font-size: 16px; color: var(--secondary-text-color);
          margin-left: 8px;
        }
        details.collapsible[open] > summary::after { content: "▴"; }
        details.collapsible > summary::-webkit-details-marker { display: none; }
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
        .toast {
          position: sticky; top: 16px;
          background: var(--success-color, #2e7d32); color: #fff;
          padding: 10px 14px; border-radius: 8px; margin-bottom: 16px;
          box-shadow: 0 4px 12px rgba(0,0,0,0.25); font-weight: 600;
        }
        .audit-row {
          display: flex; align-items: baseline; justify-content: space-between;
          padding: 8px 0; border-bottom: 1px solid var(--divider-color, #333);
        }
        .audit-row:last-child { border-bottom: none; }
        .audit-row .when { color: var(--secondary-text-color); font-size: 12px; margin-left: 12px; white-space: nowrap; }
        .audit-row code { font-size: 13px; background: var(--secondary-background-color, #2a2a2a);
                          padding: 1px 5px; border-radius: 4px; }
        .confirm-toggle {
          display: inline-flex; align-items: center; gap: 6px;
          font-size: 13px; color: var(--secondary-text-color);
          cursor: pointer; user-select: none; margin-right: 8px;
        }
        .confirm-toggle input { margin: 0; }
        .card-header {
          display: flex; align-items: center; justify-content: space-between;
          margin-bottom: 12px; gap: 12px;
        }
        .card-header h2 { margin: 0; font-size: 22px; }
        .card-pills {
          display: flex; flex-wrap: wrap; gap: 6px;
          padding: 0 0 12px; margin-bottom: 16px;
          border-bottom: 1px solid var(--divider-color, #333);
        }
        .card-pill {
          padding: 6px 12px; border-radius: 16px;
          background: var(--secondary-background-color, #2a2a2a);
          color: var(--secondary-text-color);
          font-size: 14px; font-weight: 500;
          cursor: pointer; user-select: none;
          transition: background 0.15s, color 0.15s, transform 0.1s;
          border: 1px solid transparent;
          display: inline-flex; align-items: center; gap: 6px;
        }
        .card-pill:hover { transform: translateY(-1px); }
        .card-pill.active {
          background: var(--primary-color, #03a9f4);
          color: white;
        }
        .card-pill.add-pill {
          background: transparent;
          border: 1px dashed var(--divider-color, #555);
          color: var(--secondary-text-color);
        }
        .card-pill.add-pill:hover {
          border-color: var(--primary-color, #03a9f4);
          color: var(--primary-color, #03a9f4);
        }
        .card-pill .item-count {
          opacity: 0.7; font-size: 12px; font-weight: 400;
        }
        .editor-section { margin-top: 20px; }
        .editor-section h3 {
          font-size: 15px; font-weight: 600; margin: 0 0 10px;
          color: var(--secondary-text-color); text-transform: uppercase;
          letter-spacing: 0.5px;
        }
        .editor-toolbar {
          display: flex; align-items: center; gap: 10px;
          margin-bottom: 12px;
        }
        .editor-toolbar input[type="text"] { flex: 1; }
        .subtabs {
          display: flex; gap: 4px; margin-bottom: 12px;
          border-bottom: 1px solid var(--divider-color, #333);
        }
        .subtab {
          padding: 8px 14px; cursor: pointer;
          color: var(--secondary-text-color); font-size: 13px; font-weight: 600;
          border-bottom: 2px solid transparent;
          margin-bottom: -1px;
          transition: color 0.15s, border-color 0.15s;
        }
        .subtab:hover { color: var(--primary-text-color); }
        .subtab.active {
          color: var(--primary-color, #03a9f4);
          border-bottom-color: var(--primary-color, #03a9f4);
        }
        .action-form {
          display: grid; gap: 8px; margin-top: 8px;
          grid-template-columns: 1fr 1fr 1fr auto;
        }
        .action-form input { min-width: 0; }
        @media (max-width: 720px) {
          .action-form { grid-template-columns: 1fr; }
        }
      </style>
      <div class="root">
        ${this._toast ? `<div class="toast">${esc(this._toast)}</div>` : ""}
        ${this._error ? `<div class="error">${esc(this._error)}</div>` : ""}

        <!--
          Pairings up top: this is what you actually touch day to day —
          approving a new device, revoking an old one. The setup info
          card lives below, collapsed once you have any pairing.
        -->
        <div class="card">
          <div class="card-header">
            <h2>Glasses pairings</h2>
            <span class="meta">${this._pairings.length} ${this._pairings.length === 1 ? "device" : "devices"}</span>
          </div>
          <div class="meta">When the glasses load the Web App, they show a 6-character code. Approve it here.</div>

          <div style="margin-top: 16px;">
            ${this._pairings.length === 0
              ? `<div class="meta">No pairings yet — launch the Web App on your glasses and a pending code will appear here.</div>`
              : this._pairings.map((p) => `
                  <div class="pair-row">
                    <div>
                      <span class="pair-code">${esc(p.code)}</span>
                      <span class="pill">${p.approved ? "approved" : "pending"}</span>
                    </div>
                    <div style="display:flex; gap:8px;">
                      ${p.approved ? "" : `<button data-action="approve-row" data-code="${esc(p.code)}" data-session="${esc(p.session_id)}">Approve</button>`}
                      <button class="danger" data-action="revoke" data-session="${esc(p.session_id)}">Revoke</button>
                    </div>
                  </div>
                `).join("")
            }
          </div>

          <details style="margin-top:14px;">
            <summary style="cursor:pointer; font-size:13px; color:var(--secondary-text-color);">
              Approve by typing a code instead
            </summary>
            <div class="approve-row" style="margin-top:10px;">
              <input type="text" placeholder="ABCDEF" data-action="code" value="${this._approveCode}" maxlength="8" style="text-transform: uppercase; font-family: monospace; font-size: 18px; letter-spacing: 4px;">
              <button data-action="approve">Approve</button>
            </div>
          </details>
        </div>

        <!--
          Dashboard editor: consolidates the old "Cards" + "Edit Card" +
          "Add Entities" + "Add Custom Action" cards into one coherent
          flow. Cards-pill row at top picks which card you're editing;
          the rest of the card is the editor for that card.
        -->
        <div class="card">
          <div class="card-header">
            <h2>Dashboard</h2>
            <button data-action="save" ${this._dirtyCards ? "" : "disabled"}>
              ${this._dirtyCards ? "Save changes" : "All saved"}
            </button>
          </div>
          <div class="meta">Each card holds up to ${MAX_ENTITIES} items (entities or custom service actions). Tap a card to edit it.</div>

          <div class="card-pills" style="margin-top: 14px;">
            ${this._cards.map(c => `
              <div class="card-pill ${this._selectedCardId === c.id ? "active" : ""}" data-action="select-card" data-card="${esc(c.id)}">
                ${esc(c.name)}
                <span class="item-count">${c.items.length}</span>
              </div>
            `).join("")}
            <div class="card-pill add-pill" data-action="add-card">+ Add card</div>
          </div>

          ${currentCard ? `
            <div class="editor-toolbar">
              <input type="text" data-action="rename-card" value="${esc(currentCard.name)}" placeholder="Card name">
              <button class="danger" data-action="remove-card" style="padding: 8px 14px;">Delete card</button>
            </div>

            <div class="editor-section">
              <h3>Items · ${currentCard.items.length}/${MAX_ENTITIES}</h3>
              ${currentCard.items.length === 0
                ? `<div class="meta">No items yet — add one below.</div>`
                : currentCard.items.map((item, idx) => {
                    const confirmCell = (() => {
                      if (item.confirm && typeof item.confirm === "object") {
                        return `<span class="pill" title="Set via YAML editor">Confirm: ${esc(describeConfirm(item.confirm))}</span>`;
                      }
                      const checked = item.confirm === true ? "checked" : "";
                      return `<label class="confirm-toggle"><input type="checkbox" data-action="toggle-confirm" data-index="${idx}" ${checked}> Confirm</label>`;
                    })();
                    if (item.type === 'entity') {
                      const s = this._hass.states[item.entity_id];
                      const name = s?.attributes.friendly_name || item.entity_id;
                      const state = s ? `${s.state}${s.attributes.unit_of_measurement ? " " + s.attributes.unit_of_measurement : ""}` : "—";
                      return `
                        <div class="selected-row">
                          <div style="flex:1; min-width:0;">
                            <div class="entity-name">${esc(name)}</div>
                            <div class="entity-id">${esc(item.entity_id)} · <span style="color:var(--primary-text-color)">${esc(state)}</span></div>
                          </div>
                          ${confirmCell}
                          <button class="secondary" data-action="remove-item" data-index="${idx}">Remove</button>
                        </div>
                      `;
                    } else if (item.type === 'action') {
                      return `
                        <div class="selected-row">
                          <div style="flex:1; min-width:0;">
                            <div class="entity-name">${esc(item.name)}</div>
                            <div class="entity-id">${esc(item.action)}${item.target ? ` · ${esc(item.target)}` : ''} <span class="pill" style="margin-left: 4px; background: rgba(3, 169, 244, 0.2); color: #03a9f4;">action</span></div>
                          </div>
                          ${confirmCell}
                          <button class="secondary" data-action="remove-item" data-index="${idx}">Remove</button>
                        </div>
                      `;
                    }
                  }).join("")
              }
            </div>

            <div class="editor-section">
              <h3>Add to this card</h3>
              <div class="subtabs">
                <div class="subtab ${this._addTab === "entity" ? "active" : ""}" data-action="add-tab" data-tab="entity">Entity</div>
                <div class="subtab ${this._addTab === "action" ? "active" : ""}" data-action="add-tab" data-tab="action">Custom action</div>
              </div>

              ${this._addTab === "entity" ? `
                <input type="text" placeholder="Search by name or entity_id…" data-action="search" value="${this._search}">
                <div class="entity-list">
                  ${matching.length === 0
                    ? `<div class="entity" style="cursor:default;">No matches.</div>`
                    : matching.map((s) => {
                        const onCard = currentCard.items.some(i => i.type === 'entity' && i.entity_id === s.entity_id);
                        return `
                        <div class="entity ${onCard ? "selected" : ""}" data-action="toggle" data-entity="${esc(s.entity_id)}">
                          <div>
                            <div class="entity-name">${esc(s.attributes.friendly_name || s.entity_id)}</div>
                            <div class="entity-id">${esc(s.entity_id)} · ${esc(s.state)}</div>
                          </div>
                          <div>${onCard ? "✓" : ""}</div>
                        </div>
                      `}).join("")
                  }
                </div>
              ` : `
                <div class="meta">Fire any HA service when tapped. Pin specific service+target pairs you want available on the glasses.</div>
                <div class="action-form">
                  <input type="text" id="action-name" placeholder="Label (e.g. Lights On)">
                  <input type="text" id="action-service" placeholder="Service (e.g. light.turn_on)">
                  <input type="text" id="action-target" placeholder="Target (optional)">
                  <button data-action="add-custom-action">Add</button>
                </div>
              `}
            </div>
          ` : `
            <div class="meta" style="margin-top: 12px;">Add a card to get started.</div>
          `}
        </div>

        <div class="card">
          <details class="collapsible">
            <summary>YAML editor</summary>
            <div class="meta" style="margin-top:12px">
              For AI-assisted edits. Paste YAML generated by an AI (or hand-edit).
              Top-level key is
              <code>cards</code>. Each card has <code>id</code>, <code>name</code>,
              and an <code>items</code> list. Items are either
              <code>{type: entity, entity_id: ...}</code> or
              <code>{type: action, name: ..., action: domain.service, target: ...}</code>.
              Click <strong>Apply</strong> to save.
            </div>
            <textarea data-action="yaml-text" rows="18"
              spellcheck="false"
              style="width:100%; box-sizing:border-box; font-family: ui-monospace, 'SF Mono', monospace;
                     font-size:13px; line-height:1.4; padding:12px;
                     background: var(--secondary-background-color, #1a1a1a);
                     color: var(--primary-text-color); border: 1px solid var(--divider-color, #444);
                     border-radius:8px; margin-top:12px; tab-size:2;"
            >${this._yamlText.replace(/&/g, "&amp;").replace(/</g, "&lt;")}</textarea>
            <div style="display:flex; gap:8px; margin-top:12px; align-items:center;">
              <button data-action="yaml-apply">Apply</button>
              <button data-action="yaml-copy" class="secondary">Copy</button>
              <button data-action="yaml-reload" class="secondary">Reload from server</button>
              ${this._yamlMessage
                ? `<span class="meta" style="margin-left:auto;">${esc(this._yamlMessage)}</span>`
                : ""}
            </div>
          </details>
        </div>

        <!--
          Setup info: redundant once you have any pairing. Defaulted open
          only on a fresh install (no pairings yet) so first-time users
          see the Web-App URL + steps right away; collapsed thereafter.
        -->
        <div class="card">
          <details class="collapsible" ${setupOpen}>
            <summary>Add to your glasses</summary>
            <div class="meta" style="margin-top:12px">
              One-time setup. Requires <strong>Meta AI app v272+</strong> and
              glasses firmware <strong>v125+</strong>. Your HA must be reachable
              on HTTPS from the open internet (Nabu Casa, Cloudflare Tunnel, or
              your own reverse proxy).
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
                  approve it in the <em>Glasses pairings</em> card above.</li>
            </ol>
          </details>
        </div>

        <div class="card">
          <details class="collapsible" ${this._audit.length > 0 ? "" : "open"}>
            <summary>Audit log</summary>
            <div class="meta" style="margin-top:12px">
              Last ${this._audit.length} events: pairing approvals, revocations,
              and card edits. Newest first. Capped at 200 entries; older events
              drop off.
            </div>
            <div style="margin-top: 12px;">
              ${this._audit.length === 0
                ? `<div class="meta">Nothing yet.</div>`
                : this._audit.map((a) => `
                    <div class="audit-row">
                      <div>${describeAudit(a)}</div>
                      <div class="when">${esc(timeAgo(a.ts))}</div>
                    </div>
                  `).join("")}
            </div>
          </details>
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

    // Wire up event handlers (delegated). INPUT/TEXTAREA use the "input"
    // event so we see keystrokes; everything else listens for "click".
    this.querySelectorAll("[data-action]").forEach((el) => {
      const isField = el.tagName === "INPUT" || el.tagName === "TEXTAREA";
      el.addEventListener(isField ? "input" : "click", (evt) => {
        const action = el.dataset.action;
        if (action === "select-card") {
          this._selectedCardId = el.dataset.card;
          this._render();
        } else if (action === "remove-card") {
          if (confirm("Delete this card?")) {
            this._cards = this._cards.filter(c => c.id !== this._selectedCardId);
            this._selectedCardId = this._cards.length > 0 ? this._cards[0].id : null;
            this._dirtyCards = true;
            this._render();
          }
        } else if (action === "add-card") {
          const newCard = {
            id: Math.random().toString(36).substring(2, 9),
            name: "New Card",
            items: []
          };
          this._cards.push(newCard);
          this._selectedCardId = newCard.id;
          this._dirtyCards = true;
          this._render();
        } else if (action === "rename-card") {
          const card = this._cards.find(c => c.id === this._selectedCardId);
          if (card) {
            card.name = el.value;
            this._dirtyCards = true;
            // No _render() to keep input focus.
          }
        } else if (action === "remove-item") {
          const card = this._cards.find(c => c.id === this._selectedCardId);
          if (card) {
            card.items.splice(parseInt(el.dataset.index, 10), 1);
            this._dirtyCards = true;
            this._render();
          }
        } else if (action === "add-tab") {
          // Switch the Add section between "Entity" and "Custom action"
          // sub-tabs without losing other inputs.
          this._addTab = el.dataset.tab === "action" ? "action" : "entity";
          this._render();
        } else if (action === "toggle-confirm") {
          // Per-item "Require confirmation on glasses" toggle. We don't
          // re-render on each click because that would lose the checkbox's
          // visual state mid-animation; we just mark dirty so the next
          // Save persists it.
          const card = this._cards.find(c => c.id === this._selectedCardId);
          const idx = parseInt(el.dataset.index, 10);
          const item = card?.items?.[idx];
          if (!item) return;
          if (el.checked) {
            item.confirm = true;
          } else {
            delete item.confirm;
          }
          this._dirtyCards = true;
        } else if (action === "add-custom-action") {
          const card = this._cards.find(c => c.id === this._selectedCardId);
          if (!card) return;
          if (card.items.length >= MAX_ENTITIES) return;
          const nameInput = this.querySelector('#action-name');
          const serviceInput = this.querySelector('#action-service');
          const targetInput = this.querySelector('#action-target');
          if (nameInput && nameInput.value && serviceInput && serviceInput.value) {
            const newItem = {
              type: 'action',
              name: nameInput.value,
              action: serviceInput.value
            };
            if (targetInput && targetInput.value) {
              newItem.target = targetInput.value;
            }
            card.items.push(newItem);
            nameInput.value = '';
            serviceInput.value = '';
            if (targetInput) targetInput.value = '';
            this._dirtyCards = true;
            this._render();
          }
        } else if (action === "toggle") {
          this._toggleItem(el.dataset.entity);
        } else if (action === "save") {
          this._saveCards();
        } else if (action === "search") {
          this._search = el.value;
          const search = this._search.toLowerCase();
          const matching = this._allEntities().filter((s) => {
            if (!search) return true;
            return (s.entity_id.toLowerCase().includes(search) || (s.attributes.friendly_name || "").toLowerCase().includes(search));
          }).slice(0, 80);
          
          // Only update the last .entity-list (which is the Add Entities list)
          const lists = this.querySelectorAll(".entity-list");
          const list = lists[lists.length - 1];
          if (list) {
            const currentCard = this._cards.find(c => c.id === this._selectedCardId);
            list.innerHTML = matching.length === 0 ? `<div class="entity" style="cursor:default;">No matches.</div>` : matching.map((s) => {
                  const onCard = currentCard?.items.some(i => i.type === 'entity' && i.entity_id === s.entity_id);
                  const entityId = esc(s.entity_id);
                  const friendlyName = esc(s.attributes.friendly_name || s.entity_id);
                  const state = esc(s.state);
                  return `
                  <div class="entity ${onCard ? "selected" : ""}" data-action="toggle" data-entity="${entityId}">
                    <div>
                      <div class="entity-name">${friendlyName}</div>
                      <div class="entity-id">${entityId} · ${state}</div>
                    </div>
                    <div>${onCard ? "✓" : ""}</div>
                  </div>
                `}).join("");
            
            list.querySelectorAll("[data-action]").forEach((elNode) => {
              elNode.addEventListener("click", () => {
                if (elNode.dataset.action === "toggle") this._toggleItem(elNode.dataset.entity);
              });
            });
          }
        } else if (action === "code") {
          this._approveCode = el.value.toUpperCase();
        } else if (action === "approve") {
          if (this._approveCode) this._approve(this._approveCode);
        } else if (action === "approve-row") {
          this._approveRow(el.dataset.code, el.dataset.session);
        } else if (action === "revoke") {
          if (confirm("Revoke this pairing? The glasses will lose access.")) {
            this._revoke(el.dataset.session);
          }
        } else if (action === "copy-webapp-url") {
          const url = this.querySelector('[data-action="webapp-url"]')?.textContent ?? "";
          this._copyToClipboard(url);
        } else if (action === "yaml-text") {
          // Keep _yamlText in sync with the textarea so a re-render (e.g.
          // triggered by another action) re-renders the typed content. We
          // mark dirty so _loadAll knows not to clobber on background refresh.
          this._yamlText = el.value;
          this._yamlDirty = true;
        } else if (action === "yaml-apply") {
          this._yamlApply();
        } else if (action === "yaml-copy") {
          this._copyToClipboard(this.querySelector('[data-action="yaml-text"]')?.value ?? "");
        } else if (action === "yaml-reload") {
          this._yamlDirty = false;
          this._yamlMessage = "";
          this._loadAll();
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
