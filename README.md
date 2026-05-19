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

The glasses get a Home Assistant Long-Lived Access Token (LLAT) owned by
the user who approved the pairing. That token has full HA access — same as
any LLAT you'd issue from your profile. If a glasses pair is lost or
compromised, revoke it from the management panel (or directly under your
HA profile's *Long-lived access tokens* list).

## Status

- v1: read-only adaptive grid of 1–8 entities. Live websocket updates.
- Not yet: glasses-side interaction (toggle lights, fire scripts). Coming
  in v2 once we know what survives the keyboard-only input model.
