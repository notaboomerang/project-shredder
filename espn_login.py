"""
Log in to ESPN the real way — no password form in our app, no DevTools.

Pops a real Chromium window at ESPN's login page. You sign in normally (email +
password + MFA, whatever ESPN asks — it's ESPN's own page, we never see your
password). We poll the browser's cookie jar; the moment espn_s2 + SWID appear
(i.e. you're authenticated), we grab them, save them to the local cookie file,
and close the window. From then on the app auto-loads them every launch.

This is a NODE Playwright script (the repo already has playwright + chromium
installed for the demo recorder). We shell out to it and read the JSON it prints.
Falls back cleanly with a clear message if Playwright/Chromium is missing.
"""
from __future__ import annotations

import json
import os
import subprocess

_DIR = os.path.dirname(__file__)
_RUNNER = os.path.join(_DIR, "_espn_login_runner.js")

# Node script: open ESPN, wait until espn_s2+SWID cookies exist, print them.
_RUNNER_SRC = r"""
const { chromium } = require('playwright');
(async () => {
  const ctx = await chromium.launchPersistentContext('', {
    headless: false,
    viewport: { width: 1100, height: 820 },
    args: ['--disable-blink-features=AutomationControlled'],
  });
  const page = ctx.pages()[0] || await ctx.newPage();
  // ESPN fantasy football home routes through the OneID login when signed out
  await page.goto('https://www.espn.com/fantasy/football/', { waitUntil: 'domcontentloaded' });

  const deadlineMs = Date.now() + 5 * 60 * 1000;   // 5 minutes to log in
  let s2 = '', swid = '';
  while (Date.now() < deadlineMs) {
    const cookies = await ctx.cookies(['https://www.espn.com', 'https://espn.com',
                                       'https://fantasy.espn.com']);
    for (const c of cookies) {
      if (c.name === 'espn_s2') s2 = c.value;
      if (c.name.toUpperCase() === 'SWID') swid = c.value;
    }
    if (s2 && swid) break;
    await page.waitForTimeout(1500);
  }
  await ctx.close();
  process.stdout.write(JSON.stringify({ espn_s2: s2, swid: swid }));
})().catch(e => { process.stdout.write(JSON.stringify({ error: String(e) })); });
"""


def login(timeout_s: int = 330) -> dict:
    """Launch the login window and return {'espn_s2','swid'} or {'error':...}."""
    node = _which_node()
    if not node:
        return {"error": "Node.js not found — needed for the ESPN login window."}
    if not os.path.isdir(os.path.join(_DIR, "node_modules", "playwright")):
        return {"error": "Playwright not installed in the app "
                "(npm install playwright && npx playwright install chromium)."}
    with open(_RUNNER, "w", encoding="utf-8") as f:
        f.write(_RUNNER_SRC)
    try:
        proc = subprocess.run([node, _RUNNER], cwd=_DIR, capture_output=True,
                              text=True, timeout=timeout_s)
        out = (proc.stdout or "").strip()
        data = json.loads(out) if out.startswith("{") else {"error": out or "no output"}
        return data
    except subprocess.TimeoutExpired:
        return {"error": "Login timed out — the window stayed open too long."}
    except Exception as ex:  # noqa: BLE001
        return {"error": str(ex)}
    finally:
        try:
            os.remove(_RUNNER)
        except Exception:
            pass


def _which_node() -> str:
    from shutil import which
    return which("node") or ""
