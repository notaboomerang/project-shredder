"""
Shared ESPN HTTP helper.

ESPN's public APIs sit behind Akamai, which fingerprints the TLS/HTTP2 handshake
and returns 403 "Access Denied" to plain `requests` (even with browser headers) —
while a real browser gets 200. The fix is to impersonate a browser's TLS
handshake, which `curl_cffi` does.

Strategy (best-effort, never raises):
  1. curl_cffi with impersonate='chrome'  — defeats the Akamai fingerprint block
  2. plain requests                        — fallback if curl_cffi isn't installed
     (works on networks/machines where ESPN isn't fingerprint-blocking)

Both live_games.py and drive_data.py route through get_json() so a single fix
covers the whole app. Returns {} on any failure so callers degrade gracefully.
"""
from __future__ import annotations

from typing import Optional

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Detect curl_cffi once at import.
try:
    from curl_cffi import requests as _cffi_requests  # type: ignore
    _HAS_CFFI = True
except Exception:
    _cffi_requests = None  # type: ignore
    _HAS_CFFI = False

# Plain requests as fallback.
try:
    import requests as _requests  # type: ignore
except Exception:
    _requests = None  # type: ignore


def available() -> bool:
    """True if at least one HTTP backend is usable."""
    return _HAS_CFFI or (_requests is not None)


def backend() -> str:
    """Which backend get_json() will try first — for diagnostics/UI."""
    if _HAS_CFFI:
        return "curl_cffi"
    if _requests is not None:
        return "requests"
    return "none"


def get_json(url: str, params: Optional[dict] = None, timeout: int = 12
             ) -> dict:
    """GET a URL and return parsed JSON, or {} on any failure.

    Tries curl_cffi (browser-impersonated TLS) first to defeat Akamai's
    fingerprint block, then falls back to plain requests.
    """
    # 1) curl_cffi with Chrome impersonation
    if _HAS_CFFI and _cffi_requests is not None:
        try:
            r = _cffi_requests.get(url, params=params, timeout=timeout,
                                   impersonate="chrome")
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass  # fall through to requests

    # 2) plain requests fallback
    if _requests is not None:
        try:
            r = _requests.get(url, params=params, timeout=timeout,
                              headers={"User-Agent": _UA,
                                       "Accept": "application/json"})
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass

    return {}
