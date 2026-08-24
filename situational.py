"""
Situational context layer — the deep read a raw projection can't capture.

Two things, both surfaced as INFORMATIONAL CONTEXT on a player (never a value
bump — per KC's rule):

1. QB-RUSH VULTURE: a mobile QB (Hurts, Lamar, Jayden Daniels) siphons goal-line
   and short-yardage TDs from his RB, capping the back's rushing-TD ceiling. A
   pocket passer (Stroud, Purdy, Stafford) PROTECTS the RB's TD equity. We tier
   each team's QB and translate that into an RB context note.

2. TEAM / OC / ROLE: for notable role-change or situation backs, a hand-verified
   note (OC scheme, run-game outlook, backfield competition, bellcow vs
   committee). Seeded from live reporting; overridable via data/situational.json.

Everything degrades to neutral for unknown teams/players.
"""
from __future__ import annotations

import json
import os
from typing import Optional

_DATA = os.path.join(os.path.dirname(__file__), "data")
_OVERRIDE = os.path.join(_DATA, "situational.json")
_CACHE = os.path.join(_DATA, "situational_cache.json")

# Team -> QB rush tier. 3 = heavy rushing QB (steals RB TDs), 2 = some designed
# runs, 1 = pocket passer (RB keeps goal-line work). Seeded from known QB usage;
# override in data/situational.json under "qb_rush_tier".
_QB_RUSH_TIER = {
    # tier 3 — heavy rushers, vulture RB goal-line/short-yardage TDs
    "PHI": 3, "BAL": 3, "WAS": 3, "BUF": 3, "CHI": 3, "ATL": 3,
    # tier 2 — mobile but not TD-vultures
    "GB": 2, "JAX": 2, "ARI": 2, "SEA": 2, "MIN": 2, "CAR": 2, "LV": 2,
    "NYG": 2, "TB": 2, "DEN": 2,
    # tier 1 — pocket passers, RB TDs protected
    "HOU": 1, "SF": 1, "LAR": 1, "CIN": 1, "DAL": 1, "KC": 1, "DET": 1,
    "MIA": 1, "LAC": 1, "NYJ": 1, "NE": 1, "PIT": 1, "IND": 1, "NO": 1,
    "CLE": 1, "TEN": 1,
}

# Player-level hand-verified situational notes (role changes, OC scheme). These
# are FACTS pulled from live reporting, not model output. Overridable.
_PLAYER_CTX = {
    "David Montgomery": {
        "role": "bellcow",
        "note": ("three-down bellcow in HOU (traded from DET's Gibbs committee, "
                 "2026); OC Nick Caley to feature dual-threat. Pocket QB Stroud "
                 "protects goal-line TDs, but a shaky run-blocking line caps "
                 "efficiency — high volume, moderate ceiling."),
    },
}


def _load_override() -> dict:
    if os.path.exists(_OVERRIDE):
        try:
            with open(_OVERRIDE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _load_cache() -> dict:
    """Live-lookup results persisted to disk (fetched on demand via the app)."""
    if os.path.exists(_CACHE):
        try:
            with open(_CACHE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_lookup(name: str, note: str, role: Optional[str] = None,
                source: str = "live-lookup") -> None:
    """Persist a fetched situational note so it survives reruns/restarts.
    Called by the app after a web lookup; not a network call itself.
    If a richer built-in seed exists, keep the seed and append live detail
    rather than clobbering it."""
    seed = _PLAYER_CTX.get(name)
    if seed and seed.get("note") and note not in seed["note"]:
        note = f"{seed['note']} · live: {note}"
        role = role or seed.get("role")
    cache = _load_cache()
    cache[name] = {"note": note, "role": role, "source": source}
    os.makedirs(_DATA, exist_ok=True)
    tmp = _CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    os.replace(tmp, _CACHE)


def has_context(name: str) -> bool:
    """True if we already have a note (seed, override, or cached lookup)."""
    return player_note(name) is not None


def _resolve(name: str) -> Optional[dict]:
    """Precedence: manual override > cached live-lookup > built-in seed."""
    ov = _load_override().get("player_ctx", {})
    if name in ov:
        return ov[name]
    cache = _load_cache()
    if name in cache:
        return cache[name]
    return _PLAYER_CTX.get(name)


def qb_rush_tier(team: str) -> int:
    ov = _load_override().get("qb_rush_tier", {})
    t = (team or "").upper()
    return int(ov.get(t, _QB_RUSH_TIER.get(t, 2)))


def rb_td_context(team: str) -> tuple[str, str]:
    """For an RB on `team`, how the QB affects his TD ceiling.
    Returns (tag, note). tag in {'protected','neutral','capped'}."""
    tier = qb_rush_tier(team)
    if tier >= 3:
        return "capped", "rushing QB vultures goal-line TDs — RB TD ceiling capped"
    if tier == 1:
        return "protected", "pocket QB — RB goal-line/short-yardage TDs protected"
    return "neutral", ""


def player_note(name: str) -> Optional[str]:
    """Hand-verified/cached situational note for a specific player, or None."""
    p = _resolve(name)
    return p.get("note") if p else None


def player_role(name: str) -> Optional[str]:
    p = _resolve(name)
    return p.get("role") if p else None


def context_for(name: str, position: str, team: str) -> dict:
    """Full situational context for a player. Returns
    {'notes': [str], 'qb_rush_tier': int, 'td_tag': str}."""
    notes = []
    td_tag = ""
    pn = player_note(name)
    if pn:
        notes.append(pn)
    if position == "RB":
        td_tag, td_note = rb_td_context(team)
        if td_note:
            notes.append(td_note)
    return {"notes": notes, "qb_rush_tier": qb_rush_tier(team), "td_tag": td_tag}
