"""
Eruption Watch — flag players likely to BLOW PAST their projection this week.

A ceiling model, not a floor model. We stack independent, measurable signals
that each raise the odds of a big game, sum them into a bounded `ceiling_boost`
(projected points ABOVE baseline), and surface the players where multiple
signals align — each row carrying a plain-English "why".

SIGNALS (ordered by how much we trust them):
  1. Vegas game environment  — implied team total + game script (close/negative
     = more plays, more passing). The strongest public volume/shootout proxy.
  2. Weather / roof          — a DOME or clean weather lifts the passing ceiling;
     wind >=15mph or heavy precip caps it. Real stadium forecast (open-meteo,
     free, no key); domes auto-max (no weather risk).
  3. Matchup softness        — soft defense-vs-position (defense_vs_position).
  4. Pace / plays-per-game   — fast offenses run more plays = more chances
     (drive_data.TeamDrivePace, from play-by-play).

DELIBERATELY NOT SCORED: revenge games and QB-narrative angles — no rigorous
predictive edge exists, so they are FLAVOR TAGS only, never a boost. Injured
(OUT/IR/PUP/SUS) players are filtered out (can't erupt).

All weights + thresholds are TUNABLE — see the CONFIG block below (and the app
exposes them as sliders). Every signal contributes a BOUNDED boost so no single
one dominates. Informational ceiling read; never a VORP bump.

DATA POSTURE mirrors the rest of the engine: every external pull is guarded and
degrades to neutral (0 boost) on failure — never raises.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict, field
from typing import Optional

# ===========================================================================
# CONFIG — tunable weights + thresholds (the app surfaces these as sliders).
# Each signal's boost is CLAMPED to its max so no single signal dominates.
# ===========================================================================

# Max points each signal can add to the ceiling boost (the "weights").
W_VEGAS_MAX = 5.0        # game environment: implied total + script
W_WEATHER_MAX = 3.0      # dome / clean weather upside (or negative for bad wx)
W_MATCHUP_MAX = 4.0      # soft defense-vs-position
W_PACE_MAX = 2.5         # fast-pace extra plays

# Vegas: an implied team total at/above this = full Vegas boost; league-ish
# baseline gets ~0. Script bonus when the game projects close (dog/small spread).
VEGAS_HOT_TOTAL = 27.0   # implied team total considered a "hot" environment
VEGAS_BASE_TOTAL = 21.0  # ~league-average implied team total (0 boost point)
VEGAS_CLOSE_SPREAD = 3.5 # spread at/under this = shootout/close-script bonus

# Weather: wind/precip that caps the pass game; dome removes all downside.
WX_WIND_FADE = 15.0      # mph — at/above this the passing ceiling is capped
WX_PRECIP_FADE = 0.10    # inches/hr — at/above this, fade
WX_GOOD_TEMP_LO = 40.0   # comfortable range gets a small clean-weather bump
WX_GOOD_TEMP_HI = 80.0

# Pace: drives-per-game at/above this = full pace boost.
PACE_FAST = 12.5
PACE_BASE = 11.5

# A player lands on the Eruption Watch only when total boost >= this.
ERUPTION_MIN_BOOST = 5.0

# Positions that benefit from the pass-game-centric signals (weather/vegas lift
# passing most). RB gets a partial weather/vegas credit (game script + volume).
_PASS_POS = {"QB", "WR", "TE"}
_ALL_POS = {"QB", "RB", "WR", "TE"}


# ===========================================================================
# Stadium roof + location table (for weather). roof: 'dome' | 'retractable' |
# 'outdoor'. Retractable is treated as dome for ceiling (usually closed in bad
# weather). lat/lon for the open-meteo forecast. Home team -> stadium.
# ===========================================================================
_STADIUM = {
    "ARI": ("retractable", 33.5277, -112.2626),
    "ATL": ("dome", 33.7554, -84.4009),
    "BAL": ("outdoor", 39.2780, -76.6227),
    "BUF": ("outdoor", 42.7738, -78.7870),
    "CAR": ("outdoor", 35.2258, -80.8528),
    "CHI": ("outdoor", 41.8623, -87.6167),
    "CIN": ("outdoor", 39.0955, -84.5161),
    "CLE": ("outdoor", 41.5061, -81.6995),
    "DAL": ("retractable", 32.7473, -97.0945),
    "DEN": ("outdoor", 39.7439, -105.0201),
    "DET": ("dome", 42.3400, -83.0456),
    "GB": ("outdoor", 44.5013, -88.0622),
    "HOU": ("retractable", 29.6847, -95.4107),
    "IND": ("retractable", 39.7601, -86.1639),
    "JAX": ("outdoor", 30.3239, -81.6373),
    "KC": ("outdoor", 39.0489, -94.4839),
    "LV": ("dome", 36.0909, -115.1833),
    "LAC": ("dome", 33.9535, -118.3392),
    "LAR": ("dome", 33.9535, -118.3392),
    "MIA": ("outdoor", 25.9580, -80.2389),
    "MIN": ("dome", 44.9736, -93.2575),
    "NE": ("outdoor", 42.0909, -71.2643),
    "NO": ("dome", 29.9511, -90.0812),
    "NYG": ("outdoor", 40.8135, -74.0745),
    "NYJ": ("outdoor", 40.8135, -74.0745),
    "PHI": ("outdoor", 39.9008, -75.1675),
    "PIT": ("outdoor", 40.4468, -80.0158),
    "SF": ("outdoor", 37.4030, -121.9698),
    "SEA": ("outdoor", 47.5952, -122.3316),
    "TB": ("outdoor", 27.9759, -82.5033),
    "TEN": ("outdoor", 36.1665, -86.7713),
    "WAS": ("outdoor", 38.9078, -76.8645),
}


@dataclass
class EruptionSpot:
    """One player's ceiling read this week."""
    player: str
    team: str
    position: str
    opponent: str
    ceiling_boost: float                 # total projected pts above baseline
    signals: dict = field(default_factory=dict)   # signal -> (boost, why)
    flavor: list = field(default_factory=list)     # revenge/QB tags, non-scored
    note: str = ""


# ---------------------------------------------------------------------------
# Weather (open-meteo, free, no key) — guarded + cached per (team, day)
# ---------------------------------------------------------------------------

_WX_MEMO: dict = {}


def _forecast(home_team: str, game_date: Optional[str]):
    """(temp_f, precip_in_hr, wind_mph) for the home stadium around kickoff, or
    None. `game_date` = 'YYYY-MM-DD'; if None, uses the nearest ~1pm slot in the
    7-day window. Dome venues never call the API. Never raises."""
    st = _STADIUM.get((home_team or "").upper())
    if not st:
        return None
    roof, lat, lon = st
    if roof in ("dome", "retractable"):
        return ("DOME", 0.0, 0.0)     # sentinel: no weather risk

    key = (home_team, game_date)
    if key in _WX_MEMO:
        return _WX_MEMO[key]

    try:
        import requests
        url = ("https://api.open-meteo.com/v1/forecast"
               f"?latitude={lat}&longitude={lon}"
               "&hourly=temperature_2m,precipitation,wind_speed_10m"
               "&temperature_unit=fahrenheit&wind_speed_unit=mph"
               "&precipitation_unit=inch&forecast_days=7")
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        h = r.json().get("hourly", {})
        times = h.get("time", [])
        if not times:
            _WX_MEMO[key] = None
            return None
        # pick the game-day ~13:00 slot, else the first afternoon slot
        idx = None
        for i, t in enumerate(times):
            if game_date and t.startswith(game_date) and t.endswith("13:00"):
                idx = i
                break
        if idx is None:
            for i, t in enumerate(times):
                if t.endswith("13:00"):
                    idx = i
                    break
        if idx is None:
            idx = 0
        out = (float(h["temperature_2m"][idx]),
               float(h["precipitation"][idx]),
               float(h["wind_speed_10m"][idx]))
        _WX_MEMO[key] = out
        return out
    except Exception:
        _WX_MEMO[key] = None
        return None


# ---------------------------------------------------------------------------
# Signal scorers — each returns (boost, why) with boost clamped to its max
# ---------------------------------------------------------------------------

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _vegas_signal(pos, implied_total, spread):
    """Implied team total (volume proxy) + close-script bonus."""
    if implied_total is None:
        return 0.0, ""
    span = max(1.0, VEGAS_HOT_TOTAL - VEGAS_BASE_TOTAL)
    frac = (implied_total - VEGAS_BASE_TOTAL) / span      # 0 at base, 1 at hot
    boost = _clamp(frac, 0.0, 1.2) * W_VEGAS_MAX
    why = f"implied team total {implied_total:.1f}"
    if spread is not None and spread <= VEGAS_CLOSE_SPREAD:
        boost += 0.15 * W_VEGAS_MAX                        # close-game script
        why += f", close script (spread {spread:g})"
    boost = _clamp(boost, 0.0, W_VEGAS_MAX)
    # RBs get partial credit (volume yes, but less pass-game leverage)
    if pos == "RB":
        boost *= 0.7
    return round(boost, 2), why


def _weather_signal(pos, wx):
    """Dome/clean weather lifts pass ceiling; wind/precip caps it. Can be
    NEGATIVE (a fade) for bad weather on pass-catchers."""
    if wx is None:
        return 0.0, ""
    temp, precip, wind = wx[0], wx[1], wx[2]
    pass_side = pos in _PASS_POS
    if temp == "DOME":
        return (round(W_WEATHER_MAX * (1.0 if pass_side else 0.5), 2),
                "dome — no weather risk")
    # outdoor
    if wind >= WX_WIND_FADE or precip >= WX_PRECIP_FADE:
        fade = -W_WEATHER_MAX * (1.0 if pass_side else 0.4)
        bits = []
        if wind >= WX_WIND_FADE:
            bits.append(f"wind {wind:.0f}mph")
        if precip >= WX_PRECIP_FADE:
            bits.append(f"precip {precip:.2f}in")
        return round(fade, 2), "fade: " + ", ".join(bits)
    # clean, comfortable weather = small positive
    if WX_GOOD_TEMP_LO <= temp <= WX_GOOD_TEMP_HI and pass_side:
        return round(0.5 * W_WEATHER_MAX, 2), f"clean weather ({temp:.0f}F)"
    return 0.0, ""


def _matchup_signal(pos, opponent, season, reception):
    """Reuse defense_vs_position: soft matchup = ceiling raiser."""
    try:
        import defense_vs_position as DVP
        n = DVP.matchup_nudge(opponent, pos, season, reception)
    except Exception:
        return 0.0, ""
    # map the surplus (pts/g vs league) onto the matchup weight; soft only lifts
    if n.surplus_pg <= 0:
        return 0.0, ""
    # ~+6 pts/g surplus ≈ full weight
    boost = _clamp(n.surplus_pg / 6.0, 0.0, 1.0) * W_MATCHUP_MAX
    return round(boost, 2), f"soft vs {opponent} (+{n.surplus_pg:g} pts/g)"


def _pace_signal(team):
    """Fast offense = more plays. From drive_data.TeamDrivePace neutral pace."""
    try:
        import drive_data as DD
        p = DD.get_pace(team) if hasattr(DD, "get_pace") else None
    except Exception:
        p = None
    if p is None:
        return 0.0, ""
    # no neutral field on TeamDrivePace — neutral pace = mean of lead/trail
    dpg = (getattr(p, "lead_drives_per_game", PACE_BASE)
           + getattr(p, "trail_drives_per_game", PACE_BASE)) / 2.0
    span = max(0.5, PACE_FAST - PACE_BASE)
    frac = (dpg - PACE_BASE) / span
    boost = _clamp(frac, 0.0, 1.0) * W_PACE_MAX
    if boost <= 0:
        return 0.0, ""
    return round(boost, 2), f"fast pace (~{dpg:.1f} drives/g)"



# ---------------------------------------------------------------------------
# Flavor tags (NOT scored) — narrative only, clearly labeled
# ---------------------------------------------------------------------------

def _flavor_tags(player, team, opponent, injury_of):
    """Non-predictive narrative tags: revenge angle, QB status, division game.
    Never affect the score — surfaced so the user has context, explicitly
    labeled as flavor."""
    tags = []
    # division game — deterministic, factual context (no scoring effect)
    try:
        from divisions import division_tag as _dtag
        _dt = _dtag(team, opponent)
        if _dt:
            tags.append(_dt)
    except Exception:
        pass
    try:
        inj = injury_of(player) if injury_of else None
        chip = getattr(inj, "chip", "") if inj else ""
        if chip in ("Q", "D"):
            tags.append(f"{chip} tag — game-time call")
    except Exception:
        pass
    return tags


# ---------------------------------------------------------------------------
# Main accessor
# ---------------------------------------------------------------------------

def eruption_watch(players, week=None, season=None, reception: float = 0.5,
                   top_n: int = 12, game_env_of=None, game_date_of=None,
                   opponent_of=None, home_of=None, injury_of=None):
    """Rank a slate by CEILING BOOST — players most likely to smash projection.

    Resolvers (all optional, decoupled like the screener):
      opponent_of(team, week) -> opp defense           (default: matchups schedule)
      home_of(team, week)     -> home team of the game  (for stadium/weather)
      game_env_of(team, week) -> (implied_total, spread) Vegas env, or None
      game_date_of(team, week)-> 'YYYY-MM-DD' kickoff day (for the forecast)
      injury_of(name)         -> injury obj (OUT/IR filtered; Q/D -> flavor)

    Returns {"week", "spots": [EruptionSpot...] sorted by boost desc, "config"}.
    Never raises; a player missing team/opponent/position is skipped, OUT/IR
    players are dropped, and any failed signal contributes 0.
    """
    wk = week if week is not None else _current_week()
    opponent_of = opponent_of or _default_opponent_of()
    home_of = home_of or opponent_of        # fallback: can't tell home; weather may no-op
    injury_of = injury_of or _default_injury_of()

    _OUT = {"O", "IR", "PUP", "SUS", "DNR"}

    def _attr(p, n):
        v = getattr(p, n, None)
        if v is None and isinstance(p, dict):
            v = p.get(n)
        return v

    spots = []
    seen = set()
    for p in players or []:
        try:
            name = _attr(p, "name")
            team = (_attr(p, "team") or "").upper()
            pos = (_attr(p, "position") or "").upper()
            if not name or not team or pos not in _ALL_POS:
                continue
            if (name, pos) in seen:
                continue
            seen.add((name, pos))

            # injury gate
            try:
                inj = injury_of(name)
            except Exception:
                inj = None
            if inj is not None and getattr(inj, "chip", "") in _OUT:
                continue

            opp = (opponent_of(team, wk) or "").upper()
            if not opp:
                continue

            # who's home? (for stadium/weather) — home_of may return opp or team
            home = ""
            try:
                home = (home_of(team, wk) or "").upper()
            except Exception:
                home = ""
            # if home_of just mirrors opponent_of, guess home = team unless we
            # know otherwise; weather no-ops safely if stadium unknown.
            wx_home = home if home in _STADIUM else team

            # Vegas env
            implied_total = spread = None
            if game_env_of:
                try:
                    env = game_env_of(team, wk)
                    if env:
                        implied_total, spread = env
                except Exception:
                    pass

            gdate = None
            if game_date_of:
                try:
                    gdate = game_date_of(team, wk)
                except Exception:
                    gdate = None

            wx = _forecast(wx_home, gdate)

            signals = {}
            b1, w1 = _vegas_signal(pos, implied_total, spread)
            if w1:
                signals["vegas"] = (b1, w1)
            b2, w2 = _weather_signal(pos, wx)
            if w2:
                signals["weather"] = (b2, w2)
            b3, w3 = _matchup_signal(pos, opp, season, reception)
            if w3:
                signals["matchup"] = (b3, w3)
            b4, w4 = _pace_signal(team)
            if w4:
                signals["pace"] = (b4, w4)

            boost = round(b1 + b2 + b3 + b4, 2)
            flavor = _flavor_tags(name, team, opp, injury_of)

            why = "; ".join(f"{w} (+{b:g})" if b >= 0 else f"{w} ({b:g})"
                            for _k, (b, w) in signals.items())
            spots.append(EruptionSpot(
                player=name, team=team, position=pos, opponent=opp,
                ceiling_boost=boost, signals=signals, flavor=flavor,
                note=f"{name} ({pos}, {team}) vs {opp}: {why or 'no signal'}"))
        except Exception:
            continue

    spots = [s for s in spots if s.ceiling_boost >= ERUPTION_MIN_BOOST]
    spots.sort(key=lambda s: s.ceiling_boost, reverse=True)
    cfg = {"W_VEGAS_MAX": W_VEGAS_MAX, "W_WEATHER_MAX": W_WEATHER_MAX,
           "W_MATCHUP_MAX": W_MATCHUP_MAX, "W_PACE_MAX": W_PACE_MAX,
           "ERUPTION_MIN_BOOST": ERUPTION_MIN_BOOST}
    return {"week": wk, "spots": spots[:top_n], "config": cfg}


# reuse the screener's schedule/injury/week helpers so the two stay in lockstep
def _current_week():
    try:
        from defense_vs_position import _current_week as _cw
        return _cw()
    except Exception:
        return 1


def _default_opponent_of():
    try:
        from defense_vs_position import _default_opponent_of as _o
        return _o()
    except Exception:
        return lambda _t, _w: ""


def _default_injury_of():
    try:
        from defense_vs_position import _default_injury_of as _i
        return _i()
    except Exception:
        return lambda _n: None


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    _seasons = [int(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else None
    slate = [
        {"name": "Ja'Marr Chase", "team": "CIN", "position": "WR"},
        {"name": "Amon-Ra St. Brown", "team": "DET", "position": "WR"},
        {"name": "Josh Allen", "team": "BUF", "position": "QB"},
        {"name": "Nico Collins", "team": "HOU", "position": "WR"},
    ]
    # synthetic resolvers: DET home (dome), CIN@DAL, hot totals
    opp = {"CIN": "DAL", "DET": "CHI", "BUF": "MIA", "HOU": "IND"}
    home = {"CIN": "DAL", "DET": "DET", "BUF": "BUF", "HOU": "IND"}
    env = {"CIN": (28.5, 2.5), "DET": (27.0, 6.0), "BUF": (26.0, 7.0),
           "HOU": (24.0, 3.0)}
    res = eruption_watch(
        slate, week=1, season=_seasons,
        opponent_of=lambda t, w: opp.get(t, ""),
        home_of=lambda t, w: home.get(t, ""),
        game_env_of=lambda t, w: env.get(t),
        game_date_of=lambda t, w: None)
    print(f"Eruption Watch — week {res['week']}  (min boost {res['config']['ERUPTION_MIN_BOOST']})")
    for s in res["spots"]:
        print(f"  🌋 {s.player:20} {s.position} {s.team}->{s.opponent}  "
              f"BOOST +{s.ceiling_boost}")
        for k, (b, w) in s.signals.items():
            print(f"       {k:8} {b:+.1f}  {w}")
