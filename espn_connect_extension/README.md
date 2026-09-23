# Shredder ESPN Connect — one-click login extension

A tiny Chrome/Edge extension that connects your ESPN fantasy league to
**Project Shredder** in one click — no copy/pasting cookies, no DevTools.

## Why an extension (and not a bookmarklet)

ESPN's login cookies — `espn_s2` and `SWID` — are marked **HttpOnly**. That's a
browser security flag that blocks page JavaScript (and therefore any bookmarklet)
from reading them via `document.cookie`. The **only** legitimate way to read an
HttpOnly cookie is the browser's privileged `cookies` API, which is available to
an extension but not to a bookmarklet. That's why this is a (very small)
extension instead of a bookmark.

It reads only your ESPN cookies, and only when you click the button. It sends
them nowhere except into a new Shredder tab as URL params, which Shredder ingests
via its existing one-tap `?espn_s2=…&swid=…` path and then scrubs from the
address bar. Nothing is stored on any server.

## Install (one time, ~30 seconds)

**Chrome or Edge:**
1. Go to `chrome://extensions` (Edge: `edge://extensions`).
2. Turn on **Developer mode** (top-right toggle).
3. Click **Load unpacked** and select this `espn_connect_extension` folder.
4. The 🏈 Shredder icon appears in your toolbar. (Click the puzzle-piece and pin
   it so it's always visible.)

## Use it (every time)

1. Be logged in at `fantasy.espn.com` (any ESPN tab open, or just logged in).
2. Click the 🏈 **Shredder** toolbar icon → **Connect to Shredder**.
3. A new tab opens on Shredder, already connected. Done.

If it says "Couldn't find your ESPN login," open `fantasy.espn.com`, log in, and
click again.

## Files

- `manifest.json` — MV3 manifest; `cookies` + `tabs` permissions, scoped to
  `*.espn.com` and the Shredder host only.
- `popup.html` / `popup.js` — the button + the cookie read → open-tab logic.
- `icon48.png` / `icon128.png` — toolbar icons.

## Changing the Shredder URL

If you host Shredder somewhere other than `https://shreddies.streamlit.app`,
edit `SHREDDER_URL` at the top of `popup.js` and the `host_permissions` entry in
`manifest.json` to match, then reload the extension.
