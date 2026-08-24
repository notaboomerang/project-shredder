"""
On-demand live situational lookup — fetches a player's current-team + latest
news from ESPN's FREE (no-auth) endpoints, composes a note, and caches it via
situational.save_lookup so it survives reruns/restarts.

This is the "🔎 Deep read" button's backend. It is a NETWORK call, so the app
only invokes it on an explicit click — never on every render. Facts only
(team, position, recent headline); it never invents a role or inflates value.
"""
from __future__ import annotations

import requests

import situational as SIT

_UA = {"User-Agent": "Mozilla/5.0 (ProjectShredder situational lookup)"}
_SEARCH = "https://site.web.api.espn.com/apis/search/v2"
_NEWS = ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/"
         "athletes/{athlete_id}/news")
_CORE = ("https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/"
         "athletes/{athlete_id}")


def _find_athlete_id(name: str) -> str | None:
    try:
        r = requests.get(_SEARCH, params={"query": name, "limit": 5},
                         headers=_UA, timeout=8)
        r.raise_for_status()
        for section in r.json().get("results", []):
            for item in section.get("contents", []):
                if item.get("type") == "player" and "football" in (
                        item.get("sport", "") or "").lower():
                    uid = item.get("uid", "")
                    # uid like "s:20~l:28~a:3116385" -> athlete id after "a:"
                    if "a:" in uid:
                        return uid.split("a:")[-1]
                    lnk = item.get("link", {}).get("web", "")
                    if "/id/" in lnk:
                        return lnk.split("/id/")[1].split("/")[0]
    except Exception:
        return None
    return None


def _latest_headline(athlete_id: str) -> str | None:
    try:
        r = requests.get(_NEWS.format(athlete_id=athlete_id),
                         params={"limit": 3}, headers=_UA, timeout=8)
        r.raise_for_status()
        arts = r.json().get("articles", [])
        if arts:
            a = arts[0]
            head = (a.get("headline") or "")[:120]
            desc = (a.get("description") or "").strip()[:180]
            return f"{head} — {desc}" if desc else head
    except Exception:
        return None
    return None


def _team_role(athlete_id: str) -> tuple[str | None, str | None]:
    """Returns (team_abbrev, position) from ESPN core athlete data."""
    try:
        r = requests.get(_CORE.format(athlete_id=athlete_id), headers=_UA,
                         timeout=8)
        r.raise_for_status()
        j = r.json()
        pos = (j.get("position", {}) or {}).get("abbreviation")
        team = None
        tref = (j.get("team", {}) or {}).get("$ref")
        if tref:
            tr = requests.get(tref, headers=_UA, timeout=8)
            if tr.ok:
                team = tr.json().get("abbreviation")
        return team, pos
    except Exception:
        return None, None


def deep_read(name: str, position: str, team: str) -> dict:
    """Live-fetch a situational read for `name`. Returns
    {'ok': bool, 'note': str, 'team': str, 'source': str}.
    Caches the composed note on success."""
    aid = _find_athlete_id(name)
    if not aid:
        return {"ok": False, "note": "no ESPN athlete match found",
                "team": team, "source": "espn"}

    live_team, live_pos = _team_role(aid)
    headline = _latest_headline(aid)
    use_team = live_team or team
    use_pos = live_pos or position

    bits = []
    if live_team and team and live_team.upper() != team.upper():
        bits.append(f"ESPN shows current team {live_team} (pool had {team})")
    elif live_team:
        bits.append(f"current team {live_team}")

    # QB-rush TD context for the resolved team (RB only)
    if use_pos == "RB":
        tag, td_note = SIT.rb_td_context(use_team)
        if td_note:
            bits.append(td_note)

    if headline:
        bits.append(f"latest: {headline}")

    if not bits:
        return {"ok": False, "note": "no live detail returned",
                "team": use_team, "source": "espn"}

    note = "; ".join(bits)
    SIT.save_lookup(name, note, source="espn-live")
    return {"ok": True, "note": note, "team": use_team, "source": "espn-live"}
