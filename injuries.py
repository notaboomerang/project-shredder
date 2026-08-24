"""
Live injury / health layer (Sleeper free API).

Sleeper's /players/nfl endpoint carries per-player injury_status,
injury_body_part, injury_notes, practice_participation, and news_updated —
free, no auth. We fetch it once (5MB, cache daily), normalize each player to an
ESPN-style status chip (O / IR / D / Q / PUP / SUS), build a short narrative,
and expose a composite downgrade so an actively-injured player isn't ranked at
face value.

Matching to our pool is by normalized name (live_feed._norm), so it lines up
with the FantasyPros/FFC pool regardless of suffix/punctuation differences.
Fails gracefully (no data -> everyone shows healthy).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore

_SLEEPER = "https://api.sleeper.app/v1/players/nfl"
_UA = "Mozilla/5.0 FantasyDraftAssistant/1.0"

# Sleeper injury_status -> (chip, color-class, label)
_STATUS = {
    "Out": ("O", "inj-out", "Out"),
    "IR": ("IR", "inj-out", "Injured Reserve"),
    "PUP": ("PUP", "inj-out", "Physically Unable to Perform"),
    "Sus": ("SUS", "inj-out", "Suspended"),
    "Suspended": ("SUS", "inj-out", "Suspended"),
    "Doubtful": ("D", "inj-doubt", "Doubtful"),
    "Questionable": ("Q", "inj-quest", "Questionable"),
    "DNR": ("DNR", "inj-doubt", "Did Not Report"),
}

_cache: dict = {}
_cache_ts: float = 0.0


@dataclass
class Injury:
    chip: str            # O / IR / D / Q / ...
    css: str             # css class for coloring
    label: str           # full label
    body_part: str
    narrative: str


def _norm(name: str) -> str:
    try:
        import live_feed
        return live_feed._norm(name)
    except Exception:
        import re
        return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def load_sleeper(ttl: float = 43200.0) -> dict:
    """Fetch + cache the Sleeper player map (default 12h TTL). {} on failure."""
    global _cache, _cache_ts
    if _cache and (time.time() - _cache_ts) < ttl:
        return _cache
    if requests is None:
        return {}
    try:
        r = requests.get(_SLEEPER, headers={"User-Agent": _UA}, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return _cache or {}
    # index by normalized name; keep only players with a name
    idx = {}
    for _pid, p in data.items():
        nm = p.get("full_name") or (
            (p.get("first_name", "") + " " + p.get("last_name", "")).strip())
        if nm:
            idx[_norm(nm)] = p
    _cache = idx
    _cache_ts = time.time()
    return idx


def injury_for(name: str) -> Optional[Injury]:
    idx = load_sleeper()
    p = idx.get(_norm(name))
    if not p:
        return None
    status = p.get("injury_status")
    if not status or status in ("Healthy", "Active", ""):
        return None
    chip, css, label = _STATUS.get(status, (status[:3].upper(), "inj-quest", status))
    body = p.get("injury_body_part") or ""
    notes = p.get("injury_notes") or ""
    practice = p.get("practice_participation") or ""
    bits = [label]
    if body:
        bits.append(body)
    if practice:
        bits.append(f"practice: {practice}")
    if notes:
        bits.append(notes)
    narrative = " — ".join(str(b) for b in bits if b)
    return Injury(chip=chip, css=css, label=label, body_part=body,
                  narrative=narrative)


def composite_penalty(name: str) -> float:
    """Downgrade for the Edge Engine composite based on live status."""
    inj = injury_for(name)
    if not inj:
        return 0.0
    if inj.chip in ("O", "IR", "PUP", "SUS", "DNR"):
        return -25.0     # don't draft an out/IR guy at face value
    if inj.chip == "D":
        return -8.0
    if inj.chip == "Q":
        return -3.0
    return 0.0
