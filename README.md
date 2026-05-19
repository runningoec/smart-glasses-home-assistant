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

Then to finish setup:

5. Open the **Smart Glasses** panel in the sidebar → pick up to 8 entities → Save.
6. Register the glasses-side Web App URL with Meta:
   - Wearables Developer Center → your project → Web App URL:
     `https://<your-ha-public-domain>/smart-glasses-app`
   - HA needs to be reachable on HTTPS (Nabu Casa, Cloudflare Tunnel, or
     your own reverse proxy — whatever you already use to reach HA from
     outside).
7. Load the Web App on the glasses → a 6-character pairing code appears →
   from your phone, open the Smart Glasses panel and type the code in the
   **Pairings** section → **Approve**. The glasses pick up the access token
   on their next poll and switch to the live grid.

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
