# Smart Glasses for Home Assistant

[![HACS Custom][hacs-shield]][hacs-link]

A HACS integration that adds a small Web App for the Meta Ray-Ban Display
(and any other 600x600 glasses-style HUD) directly to your Home Assistant
instance. Pair the glasses to HA once via a phone, then glance at any 1–8
entities live.

[hacs-shield]: https://img.shields.io/badge/HACS-Custom-orange.svg
[hacs-link]:   https://github.com/hacs/integration

## What you get

- **Management panel** at `<your-ha>/smart-glasses` — pick which entities the
  glasses should show, manage active pairings.
- **Glasses Web App** at `<your-ha>/smart-glasses-app` — what the glasses
  load. First time: shows a short pairing code. After: an adaptive grid of
  the entities you chose, live-updating over HA's websocket.

## Why a HACS integration (and not a separate cloud app)?

- Uses **HA's own user system** for auth — no Cloudflare Tunnel, no separate
  identity layer. The phone you pair from logs into your HA exactly the way
  you always log into HA.
- Lives **inside your HA**. No external dependencies, no public site to keep
  running, no secrets to rotate. Stops when HA stops; restarts with it.
- **Distributable**. Once installed via HACS, anyone with HA can use this.

## Install

1. **HACS** → ⋮ → **Custom repositories** → URL
   `https://github.com/runningoec/hacs-smart-glasses`, category **Integration** → **Add**.
2. Find "Smart Glasses" in HACS → **Download**.
3. Restart Home Assistant when prompted.
4. **Settings → Devices & Services → + Add Integration → "Smart Glasses"** —
   one click, no questions. The panel appears in your sidebar.

Then:

5. Open the **Smart Glasses** panel in the sidebar → pick up to 8 entities → **Save**.

## Add to your glasses

You need a way for your HA to be reachable from the open internet on HTTPS
(Nabu Casa, a Cloudflare Tunnel, or your own reverse proxy with a TLS cert
— whatever you already use to reach HA from outside the house). The
glasses fetch the Web App from that URL.

**Minimum versions**: Meta Ray-Ban Display firmware ≥ `v125`, Meta AI app ≥ `v272`.

### 1. Enable Developer Mode on your phone (one-time)

Skip this if Developer Mode is already on.

1. Open the **Meta AI app**.
2. **Settings → App Info** → tap the **app version number 5 times in a row**.
3. Confirm the prompt that appears. Developer Mode now persists across sessions.

### 2. Add the Web App

1. **Meta AI app → App Settings → App Connections → Web Apps → Add a Web App**.
2. **App name**: `Smart Glasses HA` (or whatever you like).
3. **URL**: `https://<your-ha-public-domain>/smart-glasses-app`
4. Tap **Connect**.

The app appears immediately at the bottom of your Meta Ray-Ban Display app grid.

### 3. Pair the glasses to HA (one-time per glasses)

1. Launch the app on the glasses. You'll see a **6-character pairing code**
   (e.g. `R7P9XQ`) and a hint pointing to `<your-ha>/smart-glasses`.
2. On your phone, open `<your-ha>/smart-glasses` (you're already logged in
   to HA so the panel just opens).
3. In the **Pairings** section, type the code → **Approve**.
4. Within a couple seconds the glasses switch from the pairing screen to
   the live entity grid. Pairing is sticky — the glasses remember the
   token and skip step 3 from now on.

### Re-pair / hand off to a different account

On the glasses, hold **Shift + Escape** in the Web App to wipe local
credentials and start over. From the HA panel, **Revoke** kills the
token at the HA side too (the long-lived access token is removed from
the approving user's profile).

## Pairing security

Approval mints a 256-bit random session token. Only the **hash** of that
token is stored on your HA — the plaintext is handed to the glasses once
through `/pair/<id>/token` and then wiped from server-side state. Any
glasses-side API call is Bearer-authenticated against the hash.

The token's scope is exactly **the entities + actions currently on a card**.
It cannot enumerate the rest of your HA, can't fire arbitrary services,
can't subscribe to events for unrelated entities. The glasses never talk to
HA's native `/api/*` — every call goes through scope-limited proxies:

| Glasses call | Backend behavior |
|---|---|
| `GET /api/smart_glasses/glance/cards`        | Returns current cards. |
| `GET /api/smart_glasses/glance/states`       | Returns states **only** for entity_ids that appear on a card. |
| `POST /api/smart_glasses/glance/call_service` | Rejects any (domain, service, target) tuple that isn't either (a) `homeassistant.toggle` against an entity item on a card, or (b) an exact match for an action item on a card. |
| `WS  /api/smart_glasses/glance/ws`            | First message authenticates; thereafter streams `state_changed` events filtered to card entities. |

Revoking from the panel deletes the hash. The next glasses-side call gets
401 and re-pair kicks in.

### Reverse-proxy caveat

`POST /api/smart_glasses/pair/start` is rate-limited at 6 requests/min
**per source IP**. If your HA sits behind a reverse proxy (Cloudflare
Tunnel, Nabu Casa, Traefik, …) without `use_x_forwarded_for: true` in
your HA HTTP config, every request will look like it's coming from the
proxy's IP — so the per-IP limit collapses to a single global bucket
covering *all* clients. The hard cap of **50 pending pairings** (auto-
pruned after 30s of inactivity) is the real backstop in that case;
worst-case spam is ~50 records and ~10 KB on disk that evaporates
seconds later. Not catastrophic, but if you want per-client rate
limiting, enable `use_x_forwarded_for` and list your proxy under
`trusted_proxies` in HA's `http:` config.

## Status

- v1: read-only adaptive grid of 1–8 entities. Live websocket updates.
- Not yet: glasses-side interaction (toggle lights, fire scripts). Coming
  in v2 once we know what survives the keyboard-only input model.
