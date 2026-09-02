"""
Live Games + Betting Lines + Upset Radar.

Pulls ESPN's FREE, no-auth NFL scoreboard (site.api.espn.com) which carries per
game: live score, quarter/clock, and the betting line (spread / over-under /
moneyline). No key, no cookies.

Upset Radar: for each in-progress game we compare the pregame FAVORITE against
the live game state. A favorite trailing — especially late — is a developing
upset. We compute an "upset heat" 0-100 from (a) how big a favorite they were,
(b) the live score margin against them, and (c) how little time is left. High
heat = the chalk is in real trouble; a fast-rising heat = a trend developing.

Everything degrades gracefully: network/parse failure returns [].
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import espn_http as _http

_SB = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


@dataclass
class LiveGame:
    game_id: str
    home: str
    away: str
    home_score: int
    away_score: int
    status: str            # "in", "pre", "post"
    detail: str            # "Q3 4:12" / "Final" / "Sun 1:00 PM"
    quarter: int
    clock: str
    spread: str            # e.g. "KC -6.5"
    favorite: str          # team abbrev favored, or ""
    fav_points: float      # spread magnitude (6.5)
    over_under: Optional[float]
    home_ml: Optional[float] = None       # moneyline
    away_ml: Optional[float] = None
    model_p_fav: Optional[float] = None   # OUR live win-prob for the favorite
    market_p_fav: Optional[float] = None  # de-vigged book prob for the favorite
    edge_fav: float = 0.0                 # model - market (on the fav)
    edge_note: str = ""
    upset_heat: float = 0.0
    upset_note: str = ""
    # possession/drive-based opportunity (live games only; None when unavailable)
    possessing_team: Optional[str] = None   # team abbrev with the ball right now
    fav_drives_left: Optional[float] = None # est. scoring drives left for the fav
    dog_drives_left: Optional[float] = None # est. scoring drives left for the dog
    possession_note: str = ""               # human-readable possession summary


def _get() -> dict:
    # Routes through espn_http (curl_cffi browser-impersonation) to defeat the
    # Akamai TLS-fingerprint 403 that blocks plain requests.
    return _http.get_json(_SB, timeout=10)


def _upset(fav: str, favpts: float, home: str, away: str,
           hs: int, as_: int, status: str, quarter: int, clock: str
           ) -> tuple[float, str]:
    """Heat 0-100 that the favorite is being upset, + a note. Only for live."""
    if status != "in" or not fav or favpts <= 0:
        return 0.0, ""
    fav_score = hs if fav == home else as_
    dog_score = as_ if fav == home else hs
    margin = fav_score - dog_score          # positive = fav leading
    if margin >= 0:
        # fav ahead but by less than they were favored to be = mild wobble
        wobble = max(0.0, favpts - margin)
        heat = min(45.0, wobble * 4)
        note = (f"{fav} favored by {favpts:g} but only up {margin}" if heat >= 20
                else "")
        return round(heat, 0), note
    # favorite is TRAILING — the upset is on
    deficit = -margin
    # time pressure: later + bigger deficit = hotter
    q = quarter or 1
    time_factor = min(1.0, (q - 1) / 3.0 + 0.15)      # Q1~0.15 .. Q4~1.0
    heat = min(100.0, 45 + deficit * 3.5 + favpts * 2.0 + time_factor * 20)
    when = f"Q{quarter} {clock}" if clock else f"Q{quarter}"
    note = f"🚨 {fav} (−{favpts:g} fav) TRAILING by {deficit} in {when}"
    return round(heat, 0), note


def _possession_drives(event_id: str, favorite: str, dog_team: str,
                       home: str, away: str, hs: int, as_: int,
                       quarter: int, clock: str, fav_is_home: bool
                       ) -> tuple[Optional[float], Optional[float],
                                  Optional[str], str]:
    """For a live game, fetch who has the ball and estimate scoring drives left
    for the favorite and underdog. Returns (fav_drives, dog_drives, possessing
    team abbrev, note). All None/'' if drive data is unavailable — the caller
    then falls back to the clock-only win-prob model.
    """
    try:
        import drive_data as DD
    except Exception:
        return None, None, None, ""

    lp = DD.fetch_live_possession(event_id)
    # who has the ball, mapped to 'fav' / 'dog'
    possessing = None
    poss_abbr = lp.possessing_team if lp.ok else None
    if poss_abbr == favorite:
        possessing = "fav"
    elif poss_abbr == dog_team:
        possessing = "dog"

    # favorite leading? (drives_left_for_game picks the right script split)
    fav_margin = (hs - as_) if fav_is_home else (as_ - hs)
    fav_is_leading = fav_margin >= 0

    try:
        pl = DD.drives_left_for_game(
            quarter, clock, favorite, dog_team,
            fav_is_leading=fav_is_leading, possessing=possessing)
    except Exception:
        return None, None, poss_abbr, ""

    note = ""
    if poss_abbr:
        note = f"{poss_abbr} has the ball · {favorite}≈{pl.fav_drives_left} / {dog_team}≈{pl.dog_drives_left} drives left"
    else:
        note = f"{favorite}≈{pl.fav_drives_left} / {dog_team}≈{pl.dog_drives_left} drives left"
    return pl.fav_drives_left, pl.dog_drives_left, poss_abbr, note


def fetch_live_games() -> list[LiveGame]:
    data = _get()
    games: list[LiveGame] = []
    for ev in data.get("events", []) or []:
        comp = (ev.get("competitions") or [{}])[0]
        st = (ev.get("status") or {}).get("type") or {}
        state = st.get("state", "pre")          # pre / in / post
        detail = st.get("shortDetail") or st.get("description") or ""
        status_obj = ev.get("status") or {}
        quarter = int(status_obj.get("period", 0) or 0)
        clock = status_obj.get("displayClock", "") or ""

        home = away = ""
        hs = as_ = 0
        for c in comp.get("competitors", []) or []:
            ab = (c.get("team") or {}).get("abbreviation", "")
            sc = int(c.get("score", 0) or 0)
            if c.get("homeAway") == "home":
                home, hs = ab, sc
            else:
                away, as_ = ab, sc

        # odds
        spread = ""
        favorite = ""
        favpts = 0.0
        ou = None
        home_ml = away_ml = None
        odds = (comp.get("odds") or [{}])
        if odds:
            o = odds[0]
            spread = o.get("details", "") or ""     # "KC -6.5"
            ou = o.get("overUnder")
            parts = spread.replace("PK", "-0").split()
            if len(parts) == 2:
                try:
                    favpts = abs(float(parts[1]))
                    favorite = parts[0]
                except ValueError:
                    pass
            # moneylines (shapes vary: homeTeamOdds.moneyLine, or awayTeamOdds)
            hto = o.get("homeTeamOdds") or {}
            ato = o.get("awayTeamOdds") or {}
            home_ml = hto.get("moneyLine")
            away_ml = ato.get("moneyLine")

        # ---- possession / drives-left (live games only) ----
        possessing_team = None
        fav_drives = dog_drives = None
        poss_note = ""
        fav_is_home = (favorite == home) if favorite else True
        dog_team = (away if fav_is_home else home) if favorite else ""
        if state == "in" and favorite and favpts > 0:
            fav_drives, dog_drives, possessing_team, poss_note = _possession_drives(
                ev.get("id", ""), favorite, dog_team, home, away,
                hs, as_, quarter, clock, fav_is_home)

        # ---- OUR live win-prob vs the market (the edge) ----
        import win_prob as WP_
        model_pf = market_pf = None
        edge_v, edge_nt = 0.0, ""
        if favorite and favpts > 0:
            fav_margin = (hs - as_) if fav_is_home else (as_ - hs)
            # possession-aware when we have drive data; else clock-only (backward-compat)
            model_pf = WP_.live_win_prob(
                fav_margin, favpts, quarter, clock,
                fav_drives_left=fav_drives, dog_drives_left=dog_drives)
            # market prob: prefer de-vigged moneylines, else the spread
            fav_ml = home_ml if fav_is_home else away_ml
            dog_ml = away_ml if fav_is_home else home_ml
            market_pf = WP_.devig(WP_.american_to_prob(fav_ml),
                                  WP_.american_to_prob(dog_ml))
            if market_pf is None:
                market_pf = WP_.spread_to_prob(favpts)
            edge_v, edge_nt = WP_.edge(model_pf, market_pf)

        heat, note = _upset(favorite, favpts, home, away, hs, as_,
                            state, quarter, clock)
        games.append(LiveGame(
            game_id=ev.get("id", ""), home=home, away=away,
            home_score=hs, away_score=as_, status=state, detail=detail,
            quarter=quarter, clock=clock, spread=spread, favorite=favorite,
            fav_points=favpts, over_under=ou, home_ml=home_ml, away_ml=away_ml,
            model_p_fav=(round(model_pf, 3) if model_pf is not None else None),
            market_p_fav=(round(market_pf, 3) if market_pf is not None else None),
            edge_fav=edge_v, edge_note=edge_nt,
            upset_heat=heat, upset_note=note,
            possessing_team=possessing_team,
            fav_drives_left=(round(fav_drives, 2) if fav_drives is not None else None),
            dog_drives_left=(round(dog_drives, 2) if dog_drives is not None else None),
            possession_note=poss_note))
    # live games first, then by upset heat
    games.sort(key=lambda g: (g.status != "in", -g.upset_heat))
    return games


def upset_alerts(games: Optional[list[LiveGame]] = None,
                 min_heat: float = 40.0) -> list[LiveGame]:
    games = games or fetch_live_games()
    return [g for g in games if g.status == "in" and g.upset_heat >= min_heat]
