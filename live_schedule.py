"""
Live NFL week + schedule from ESPN's public scoreboard API.

This replaces the hand-maintained data/schedule.json + calendar-arithmetic week
guess as the SOURCE OF TRUTH, killing a whole class of bugs (wrong current week,
stale schedule, a week's matchups frozen in cache). ESPN's public scoreboard
endpoint returns the authoritative current `week.number` and the real slate, so
we read it directly.

Posture mirrors the rest of the app: network first, static fallback second,
never raises. Results are cached in-process for a short TTL so a page that asks
several times in one render does not hammer ESPN.

  current_week()            -> int (1-18), live; falls back to a calendar guess.
  week_opponents(week=None) -> {TEAM_ABBR: OPP_ABBR} for that week, live; falls
                               back to matchups.load_schedule()[team][week].

Team abbreviations are normalized to the app's convention (e.g. ESPN 'WSH' ->
'WAS', 'LAR' -> 'LA') so they line up with the projection pool + schedule.json.
"""
from __future__ import annotations

import time
from typing import Optional

_SCOREBOARD = ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/"
               "scoreboard")

# ESPN uses a few abbreviations the app spells differently. Map ESPN -> app.
_ABBR_FIX = {
    "WSH": "WAS",   # Washington
    "LAR": "LA",    # Rams (app uses LA)
    "JAC": "JAX",   # Jacksonville
}

_TTL_SECS = 300
_cache: dict = {"week": None, "sched": {}, "fetched_at": 0.0}


def _norm(abbr: str) -> str:
    a = (abbr or "").upper()
    return _ABBR_FIX.get(a, a)


def _fetch(week: Optional[int] = None) -> dict:
    """Fetch ESPN scoreboard JSON for the current week (week=None) or a specific
    week. Returns {} on any failure. Never raises."""
    try:
        import requests
    except Exception:
        return {}
    params = {}
    if week:
        params = {"week": int(week), "seasontype": 2}
    try:
        r = requests.get(_SCOREBOARD, params=params or None, timeout=6)
        r.raise_for_status()
        return r.json() or {}
    except Exception:
        return {}


def _parse_opponents(data: dict) -> dict:
    """Pull {team: opponent} (both directions) out of a scoreboard payload."""
    out: dict = {}
    for ev in (data.get("events") or []):
        comps = (ev.get("competitions") or [{}])[0].get("competitors") or []
        abbrs = []
        for c in comps:
            t = (c.get("team") or {}).get("abbreviation")
            if t:
                abbrs.append(_norm(t))
        if len(abbrs) == 2:
            out[abbrs[0]] = abbrs[1]
            out[abbrs[1]] = abbrs[0]
    return out


def _refresh_if_stale() -> None:
    if (time.time() - _cache["fetched_at"]) < _TTL_SECS and _cache["week"]:
        return
    data = _fetch()
    if data:
        wk = (data.get("week") or {}).get("number")
        opps = _parse_opponents(data)
        if wk:
            _cache["week"] = int(wk)
        if opps:
            _cache["sched"] = {int(wk) if wk else 0: opps}
        _cache["fetched_at"] = time.time()


def current_week() -> int:
    """Authoritative current NFL week from ESPN; calendar-guess fallback."""
    _refresh_if_stale()
    if _cache["week"]:
        return int(_cache["week"])
    # Fallback: the calendar guess in defense_vs_position (kept as a backstop).
    try:
        from defense_vs_position import _current_week as _cw
        return _cw()
    except Exception:
        return 1


def week_opponents(week: Optional[int] = None) -> dict:
    """{TEAM: OPP} for the given week (default = current). Live from ESPN, with
    the static schedule.json as fallback. Never raises."""
    wk = int(week) if week else current_week()
    # cached current-week slate?
    if wk in _cache["sched"] and _cache["sched"][wk]:
        return dict(_cache["sched"][wk])
    # fetch that specific week
    data = _fetch(week=wk)
    opps = _parse_opponents(data) if data else {}
    if opps:
        _cache["sched"][wk] = opps
        return dict(opps)
    # static fallback: matchups.load_schedule()[team][week]
    try:
        from matchups import load_schedule
        sched = load_schedule()
        fb = {}
        for team, wkmap in (sched or {}).items():
            opp = wkmap.get(wk) or wkmap.get(int(wk))
            if opp:
                fb[_norm(team)] = _norm(opp)
        return fb
    except Exception:
        return {}


def opponent_of(week: Optional[int] = None):
    """Return a resolver opponent_of(team, week) backed by the live slate — the
    drop-in replacement for defense_vs_position._default_opponent_of()."""
    def _resolve(team, w=None):
        wk = int(w) if w else (int(week) if week else current_week())
        opps = week_opponents(wk)
        return opps.get(_norm(team), "")
    return _resolve
