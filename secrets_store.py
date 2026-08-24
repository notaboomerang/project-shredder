"""
ESPN cookie convenience store — so you enter espn_s2 / SWID at most ONCE.

Priority on startup (auto_load):
  1. A saved local file (data/espn_cookies.json) — written when you click
     "Remember on this machine".
  2. Best-effort read straight from your logged-in browser (Chrome/Edge/
     Firefox) via the optional `browser_cookie3` package, if installed.
  3. Nothing — you type them (once), then hit Remember.

SECURITY POSTURE: these are session cookies, not a password. The file lives
only on your machine, next to the app; the app binds to 127.0.0.1 so the
cookies never leave the box. Deleting the file (or the "Forget" button) wipes
them. This is the same tradeoff as a browser "stay logged in" checkbox.
"""
from __future__ import annotations

import json
import os
from typing import Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_FILE = os.path.join(_DATA_DIR, "espn_cookies.json")


def load_file() -> tuple[str, str]:
    """Return (espn_s2, swid) from the saved file, or ('','')."""
    if not os.path.exists(_FILE):
        return "", ""
    try:
        with open(_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("espn_s2", "") or "", d.get("swid", "") or ""
    except Exception:
        return "", ""


def save_file(espn_s2: str, swid: str) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_FILE, "w", encoding="utf-8") as f:
        json.dump({"espn_s2": espn_s2 or "", "swid": swid or ""}, f)
    # tighten perms where the OS supports it (best-effort)
    try:
        os.chmod(_FILE, 0o600)
    except Exception:
        pass


def forget() -> None:
    try:
        os.remove(_FILE)
    except Exception:
        pass


def has_saved() -> bool:
    s2, swid = load_file()
    return bool(s2 or swid)


def read_from_browser() -> tuple[str, str, str]:
    """Best-effort: pull espn_s2 + SWID straight from a logged-in browser.
    Returns (espn_s2, swid, message). Requires the optional browser_cookie3
    package; if it's absent or nothing is found, returns ('','', reason)."""
    try:
        import browser_cookie3 as bc3  # type: ignore
    except Exception:
        return "", "", ("browser auto-read unavailable (install browser_cookie3 "
                        "to pull cookies straight from Chrome/Edge).")
    s2 = swid = ""
    # try each browser; ESPN cookies live on the .espn.com domain
    for loader in ("chrome", "edge", "firefox", "brave"):
        try:
            jar = getattr(bc3, loader)(domain_name="espn.com")
        except Exception:
            continue
        for c in jar:
            if c.name == "espn_s2" and not s2:
                s2 = c.value
            elif c.name.upper() == "SWID" and not swid:
                swid = c.value
        if s2 and swid:
            return s2, swid, f"Loaded ESPN cookies from {loader}."
    if s2 or swid:
        return s2, swid, "Found partial cookies in your browser."
    return "", "", ("No ESPN cookies found in your browser — log into "
                    "fantasy.espn.com there first, or paste them once.")


def auto_load() -> tuple[str, str, str]:
    """Startup resolver: saved file first, then browser, then empty.
    Returns (espn_s2, swid, source)."""
    s2, swid = load_file()
    if s2 or swid:
        return s2, swid, "file"
    bs2, bswid, _msg = read_from_browser()
    if bs2 or bswid:
        return bs2, bswid, "browser"
    return "", "", "none"
