"""
Fourth-quarter clutch split — how a team performs in Q4 by GAME STATE × VENUE.

The question this answers (KC, 2026-09-03): when the HOME team is DOWN in the
4th quarter, how do they normally perform in Q4 at home — versus when they're
in front at home, in front away, and down away?

Football teams are not the same team in every game state. A home crowd behind a
comeback plays differently than a road team protecting a lead. This module cuts
each team's 4th-quarter plays into the four buckets:

    home_trailing   home_leading   away_leading   away_trailing

(plus a home_tied / away_tied fold-in so short samples still get a number), and
for each bucket computes REAL Q4 performance from nflverse play-by-play:

    epa_per_play     offensive EPA/play in Q4 in this state (the cleanest
                     single "how well are they moving the ball" signal)
    points_per_game  Q4 points scored in games that reached this state
    score_rate       fraction of Q4 offensive drives that end in a score
    comeback_rate    (trailing states only) fraction of games entering Q4
                     behind that the team went on to tie or take the lead
    n_plays / n_games  sample size, so a thin cell is visibly thin

DATA POSTURE mirrors drive_data.py: sourced from nfl_data_py play-by-play when
installed; a shipped league-average fallback keeps the feature working with no
network / no package. Every number degrades to the fallback per-cell, never
raises. This is INFORMATIONAL CONTEXT (a real-football read), never a VORP bump
— consistent with the project's core rule.

SELF-TEST:  python clutch_split.py [SEASON]
  Prints the four-bucket table for a few teams so the pbp columns can be
  verified against a real season on a machine with nfl_data_py installed.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict
from typing import Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_CACHE = os.path.join(_DATA_DIR, "clutch_split.json")

# The four states we report, plus the tied fold-ins used only to pad thin cells.
_STATES = ("home_trailing", "home_leading", "away_leading", "away_trailing")

# League-average Q4 fallback per state. Values are deliberately conservative and
# reflect the well-known asymmetries: home teams and trailing (hurry-up, pass-
# heavy, higher-variance) offenses generate more Q4 EPA/points but score-rate is
# lower when protecting a lead. Comeback_rate applies to trailing states only.
# Used ONLY when pbp is unavailable or a team-state cell is empty.
_LEAGUE_FALLBACK: dict[str, dict] = {
    "home_trailing": {"epa_per_play": 0.02, "points_per_game": 7.2,
                      "score_rate": 0.34, "comeback_rate": 0.34},
    "home_leading":  {"epa_per_play": -0.04, "points_per_game": 4.8,
                      "score_rate": 0.30, "comeback_rate": None},
    "away_leading":  {"epa_per_play": -0.05, "points_per_game": 4.4,
                      "score_rate": 0.29, "comeback_rate": None},
    "away_trailing": {"epa_per_play": -0.01, "points_per_game": 6.6,
                      "score_rate": 0.31, "comeback_rate": 0.28},
}


@dataclass
class StateSplit:
    """One team's Q4 performance in one (venue × game-state) bucket."""
    state: str
    epa_per_play: float
    points_per_game: float
    score_rate: float
    comeback_rate: Optional[float]   # trailing states only; None otherwise
    n_plays: int
    n_games: int
    source: str = "fallback"         # "pbp" | "cache" | "fallback"


@dataclass
class TeamClutch:
    """A team's four-bucket Q4 clutch profile."""
    team: str
    splits: dict                     # state -> StateSplit
    source: str = "fallback"

    def get(self, state: str) -> StateSplit:
        return self.splits.get(state) or _fallback_split(state)


def _fallback_split(state: str) -> StateSplit:
    fb = _LEAGUE_FALLBACK[state]
    return StateSplit(
        state=state,
        epa_per_play=fb["epa_per_play"],
        points_per_game=fb["points_per_game"],
        score_rate=fb["score_rate"],
        comeback_rate=fb["comeback_rate"],
        n_plays=0, n_games=0, source="fallback",
    )


def _fallback_team(team: str) -> TeamClutch:
    return TeamClutch(team=team,
                      splits={s: _fallback_split(s) for s in _STATES},
                      source="fallback")


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _load_cache() -> dict:
    if not os.path.exists(_CACHE):
        return {}
    try:
        with open(_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(d: dict) -> None:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_CACHE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def _default_season() -> int:
    import datetime as _dt
    now = _dt.date.today()
    return now.year if now.month >= 8 else now.year - 1


# ---------------------------------------------------------------------------
# Build from nflverse play-by-play
# ---------------------------------------------------------------------------

def build_clutch_table(season=None,
                       use_cache: bool = True) -> dict[str, TeamClutch]:
    """Per-team four-bucket Q4 clutch profile for one or more seasons.

    `season` may be a single int, a list/tuple of ints, or None (auto rolling
    window). Multiple seasons are RECENCY-WEIGHTED: each season is aggregated
    separately, then the per-cell values are blended with a weight of
    (season_recency_weight × that cell's play count). So the newest season leads,
    but a heavily-weighted season with a thin sample can't overwhelm a
    lighter-weighted season with far more plays — recency and sample size both
    count. Single season = that season's raw aggregate (no blend).

    Tries nfl_data_py / direct nflverse parquet; falls back to cache, then to
    league averages. Always returns a dict keyed by team abbrev; never raises.
    """
    seasons = _normalize_seasons(season)
    ckey = "+".join(str(y) for y in seasons)

    if use_cache:
        cached = _load_cache()
        teams = cached.get("teams") if isinstance(cached, dict) else None
        if teams and str(cached.get("_season")) == ckey:
            out = {}
            for t, states in teams.items():
                out[t] = TeamClutch(
                    team=t,
                    splits={s: StateSplit(**{**d, "source": "cache"})
                            for s, d in states.items()},
                    source="cache",
                )
            if out:
                return out

    # aggregate each season on its own, keep the recency weight alongside
    weights = _season_weights(seasons)
    per_season: list[tuple[float, dict[str, TeamClutch]]] = []
    for yr in seasons:
        pbp = _load_pbp(yr)
        if pbp is None:
            continue
        tbl = _aggregate_clutch(pbp)
        if tbl:
            per_season.append((weights.get(yr, 0.0), tbl))

    if per_season:
        table = (per_season[0][1] if len(per_season) == 1
                 else _blend_tables(per_season))
        if table:
            _save_cache({"_season": ckey,
                         "teams": {t: {s: asdict(sp) for s, sp in tc.splits.items()}
                                   for t, tc in table.items()}})
            return table

    return {}


def _blend_tables(per_season: list) -> dict[str, TeamClutch]:
    """Blend per-season team tables into one, weighting each season's per-cell
    value by (recency_weight × that cell's play count). n_plays/n_games are
    SUMMED across seasons (real total sample); rates/EPA are the weighted mean.
    `per_season` = list of (weight, {team: TeamClutch}).
    """
    teams = set()
    for _w, tbl in per_season:
        teams.update(tbl.keys())

    out: dict[str, TeamClutch] = {}
    for team in teams:
        splits: dict[str, StateSplit] = {}
        for state in _STATES:
            num_epa = num_scr = num_ppg = 0.0
            den_play = den_game = 0.0
            cb_num = cb_den = 0.0
            has_cb = False
            tot_plays = tot_games = 0
            real = False
            for w, tbl in per_season:
                tc = tbl.get(team)
                if not tc or state not in tc.splits:
                    continue
                c = tc.splits[state]
                if c.n_plays <= 0:
                    continue
                real = True
                pw = w * c.n_plays          # recency × sample (for play-based)
                gw = w * c.n_games          # recency × sample (for game-based)
                num_epa += c.epa_per_play * pw
                num_scr += c.score_rate * pw
                den_play += pw
                num_ppg += c.points_per_game * gw
                den_game += gw
                tot_plays += c.n_plays
                tot_games += c.n_games
                if c.comeback_rate is not None:
                    has_cb = True
                    cb_num += c.comeback_rate * gw
                    cb_den += gw
            fb = _LEAGUE_FALLBACK[state]
            if not real:
                splits[state] = _fallback_split(state)
                continue
            splits[state] = StateSplit(
                state=state,
                epa_per_play=round(num_epa / den_play, 3) if den_play else fb["epa_per_play"],
                points_per_game=round(num_ppg / den_game, 2) if den_game else fb["points_per_game"],
                score_rate=round(num_scr / den_play, 3) if den_play else fb["score_rate"],
                comeback_rate=(round(cb_num / cb_den, 3) if (has_cb and cb_den) else None),
                n_plays=tot_plays,
                n_games=tot_games,
                source="pbp",
            )
        out[team] = TeamClutch(team=team, splits=splits, source="pbp")
    return out


def _normalize_seasons(season) -> list[int]:
    """Coerce the `season` arg into a concrete list of years to pool.

    None -> auto: the rolling window from _auto_window() (current season once it
    has data, plus the two priors). An int -> [int]. A list/tuple -> that list
    (deduped, sorted desc so the newest leads)."""
    if season is None:
        return [y for y, _w in _auto_window()]
    if isinstance(season, (list, tuple, set)):
        return sorted({int(y) for y in season}, reverse=True)
    return [int(season)]


# Recency weighting: how much each season-back counts relative to the newest.
# newest season = 1.0, one year back = _DECAY, two years back = _DECAY**2, ...
# 0.55 means last year counts ~55% of this year, two years ago ~30%. Tunable.
_DECAY = 0.55
_WINDOW_YEARS = 3          # how many seasons to blend at most


def _auto_window() -> list[tuple[int, float]]:
    """Rolling (season, weight) list, newest first, chosen automatically.

    The design goal (KC): the tool should roll forward on its own — once the
    CURRENT season has real data (Week 1 in the books) it leads with full
    weight; earlier seasons get exponentially less. Before the current season
    has any data (offseason / pre-Week-1) it is dropped so we don't lead with an
    empty year, and the prior season leads instead.

    Weights follow _DECAY per year back and are normalized to sum to 1.0.
    """
    cur = _default_season()
    years = [cur - i for i in range(_WINDOW_YEARS)]

    # drop the current season if it has no play-by-play yet (pre-Week-1)
    if not _season_has_data(cur):
        years = [cur - 1 - i for i in range(_WINDOW_YEARS)]

    raw = [(y, _DECAY ** i) for i, y in enumerate(years)]
    tot = sum(w for _y, w in raw) or 1.0
    return [(y, round(w / tot, 4)) for y, w in raw]


_HAS_DATA_MEMO: dict[int, bool] = {}


def _season_has_data(year: int) -> bool:
    """True if nflverse has posted any play-by-play for `year` yet. Cheap-ish:
    a HEAD-like check via a tiny read is not possible on GitHub releases, so we
    memoize a real load attempt (the parquet is cached by _load_pbp callers).
    Errs on the side of False (treat as no-data) on any failure."""
    if year in _HAS_DATA_MEMO:
        return _HAS_DATA_MEMO[year]
    try:
        df = _load_pbp(year)
        ok = df is not None and len(df) > 0
    except Exception:
        ok = False
    _HAS_DATA_MEMO[year] = ok
    return ok


def _season_weights(seasons: list[int]) -> dict[int, float]:
    """Recency weights for an explicit season list (newest = 1.0, decaying),
    normalized to sum to 1.0. Used when the caller passed seasons directly."""
    ordered = sorted(seasons, reverse=True)
    raw = {y: _DECAY ** i for i, y in enumerate(ordered)}
    tot = sum(raw.values()) or 1.0
    return {y: w / tot for y, w in raw.items()}


def _load_pbp(year: int):
    """Load one season of nflverse play-by-play as a DataFrame, or None.

    Tries nfl_data_py first (if the environment has it), then falls back to
    reading the public nflverse-data parquet release directly — which is exactly
    what nfl_data_py does under the hood, but works when nfl_data_py can't be
    installed (its pinned pandas fails to build on Python 3.13). Only pandas +
    a parquet engine (pyarrow) are required for the direct path.
    """
    # 1) nfl_data_py if available
    try:
        import nfl_data_py as nfl  # type: ignore
        return nfl.import_pbp_data([year], downcast=True, cache=False)
    except Exception:
        pass

    # 2) direct nflverse parquet release
    try:
        import pandas as pd
        url = ("https://github.com/nflverse/nflverse-data/releases/download/"
               f"pbp/play_by_play_{year}.parquet")
        return pd.read_parquet(url)
    except Exception:
        return None


def _entering_q4_margin(pbp) -> dict:
    """Margin (team_score - opp_score) at the START of Q4, per (game, team).

    Uses the last pre-Q4 play's running score. Returns
    {(game_id, team): margin} where team is each side of the game.
    """
    pre = pbp[pbp["qtr"] <= 3].dropna(subset=["home_team", "away_team"])
    if pre.empty:
        return {}
    # last play before Q4 in each game carries end-of-Q3 running score
    last = pre.sort_values(["game_id", "qtr", "game_seconds_remaining"],
                           ascending=[True, True, False])
    last = last.groupby("game_id", as_index=False).last()
    margins = {}
    for _, r in last.iterrows():
        gid = r["game_id"]
        h, a = r["home_team"], r["away_team"]
        hs = float(r.get("total_home_score") or 0.0)
        as_ = float(r.get("total_away_score") or 0.0)
        margins[(gid, h)] = hs - as_
        margins[(gid, a)] = as_ - hs
    return margins


def _final_margin(pbp) -> dict:
    """Final margin per (game, team) from the last play's running score."""
    last = pbp.dropna(subset=["home_team", "away_team"]).sort_values(
        ["game_id", "qtr", "game_seconds_remaining"],
        ascending=[True, True, False]).groupby("game_id", as_index=False).last()
    fin = {}
    for _, r in last.iterrows():
        gid = r["game_id"]
        h, a = r["home_team"], r["away_team"]
        hs = float(r.get("total_home_score") or 0.0)
        as_ = float(r.get("total_away_score") or 0.0)
        fin[(gid, h)] = hs - as_
        fin[(gid, a)] = as_ - hs
    return fin


def _aggregate_clutch(pbp) -> dict[str, TeamClutch]:
    """Aggregate nflverse pbp into the four Q4 (venue × state) buckets per team.

    State is decided by the team's margin ENTERING Q4 (start of the 4th quarter)
    and whether the team is home or away — NOT the live margin mid-quarter — so a
    team that blows a lead still counts as 'leading' for the whole Q4, which is
    the game-script lens KC asked for.
    """
    import pandas as pd
    import numpy as np

    needed = {"game_id", "posteam", "home_team", "away_team", "qtr",
              "game_seconds_remaining", "total_home_score", "total_away_score",
              "epa", "play"}
    if not needed.issubset(set(pbp.columns)):
        return {}

    enter = _entering_q4_margin(pbp)
    finals = _final_margin(pbp)
    if not enter:
        return {}

    q4 = pbp[(pbp["qtr"] == 4) & pbp["posteam"].notna()].copy()
    if q4.empty:
        return {}

    def _state(row) -> Optional[str]:
        gid, team = row["game_id"], row["posteam"]
        m = enter.get((gid, team))
        if m is None:
            return None
        is_home = team == row["home_team"]
        if m > 0:
            return "home_leading" if is_home else "away_leading"
        if m < 0:
            return "home_trailing" if is_home else "away_trailing"
        # tied entering Q4 -> fold into BOTH leading & trailing of that venue so
        # thin cells still get signal; handled by duplicating below.
        return "home_tied" if is_home else "away_tied"

    q4["state"] = q4.apply(_state, axis=1)
    q4 = q4.dropna(subset=["state"])

    # scoring-play flag from the running-score jump on offensive plays
    q4 = q4.sort_values(["game_id", "posteam", "game_seconds_remaining"],
                        ascending=[True, True, False])

    out: dict[str, TeamClutch] = {}
    for team, g in q4.groupby("posteam"):
        splits: dict[str, StateSplit] = {}
        # comeback: of games this team ENTERED Q4 trailing (home/away), how many
        # ended tied-or-ahead (final margin >= 0)
        for state in _STATES:
            venue = "home" if state.startswith("home") else "away"
            lead_trail = "leading" if state.endswith("leading") else "trailing"
            tied_state = f"{venue}_tied"
            cell = g[g["state"].isin([state, tied_state])]
            offense = cell[cell["play"] == 1] if "play" in cell.columns else cell
            n_plays = int(len(offense))
            n_games = int(cell["game_id"].nunique())
            fb = _LEAGUE_FALLBACK[state]

            if n_plays > 0:
                epa = float(offense["epa"].mean())
            else:
                epa = fb["epa_per_play"]

            # Q4 points scored: sum of positive running-score deltas for this team
            pts, sc_drives, tot_drives = _q4_points_and_scoring(cell)
            ppg = (pts / n_games) if n_games else fb["points_per_game"]
            score_rate = (sc_drives / tot_drives) if tot_drives else fb["score_rate"]

            comeback = None
            if lead_trail == "trailing":
                trail_games = cell["game_id"].unique()
                if len(trail_games):
                    came = sum(1 for gid in trail_games
                               if finals.get((gid, team), -1) >= 0)
                    comeback = round(came / len(trail_games), 3)
                else:
                    comeback = fb["comeback_rate"]

            splits[state] = StateSplit(
                state=state,
                epa_per_play=round(epa, 3),
                points_per_game=round(ppg, 2),
                score_rate=round(score_rate, 3),
                comeback_rate=comeback,
                n_plays=n_plays,
                n_games=n_games,
                source="pbp" if n_plays > 0 else "fallback",
            )
        out[team] = TeamClutch(team=team, splits=splits, source="pbp")
    return out


def _q4_points_and_scoring(cell) -> tuple[float, int, int]:
    """From a team's Q4 plays in one state, compute (points_scored,
    scoring_drives, total_drives) using drive-level results when available,
    else running-score deltas.
    """
    import numpy as np

    # drives: prefer nflverse 'fixed_drive' + 'fixed_drive_result'
    if "fixed_drive" in cell.columns and "fixed_drive_result" in cell.columns:
        drv = cell.dropna(subset=["fixed_drive"]).groupby(
            ["game_id", "fixed_drive"], as_index=False).first()
        tot = int(len(drv))
        res = drv["fixed_drive_result"].astype(str).str.lower()
        scoring = int(res.str.contains("touchdown").sum()
                      + res.str.contains("field goal").sum())
    else:
        tot, scoring = 0, 0

    # points: sum positive jumps in this team's running score across Q4 plays
    pts = 0.0
    if "posteam_score_post" in cell.columns:
        s = cell["posteam_score_post"].ffill()
        pts = float(max(0.0, (s.max() - s.min()))) if len(s) else 0.0
    elif "posteam_score" in cell.columns:
        s = cell["posteam_score"].dropna()
        pts = float(max(0.0, (s.max() - s.min()))) if len(s) else 0.0
    return pts, scoring, tot


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------

_MEMO: dict[str, dict[str, TeamClutch]] = {}


def get_clutch(team: str, season=None) -> TeamClutch:
    """Team Q4 clutch profile with fallback. Never raises. Caches the table.
    `season` may be an int, a list of ints (pooled), or None (two most recent)."""
    key = "+".join(str(y) for y in _normalize_seasons(season))
    if key not in _MEMO:
        _MEMO[key] = build_clutch_table(season)
    return _MEMO[key].get((team or "").upper()) or _fallback_team((team or "").upper())


_BASELINE_MEMO: dict[str, dict] = {}


def league_baseline(season=None) -> dict:
    """League-wide average per state across all 32 teams, PLAY-WEIGHTED (a team
    with more plays in a state counts more — matching how the team cells were
    built). Returns {state: {epa_per_play, points_per_game, score_rate,
    comeback_rate, n_teams, n_plays}}. Falls back to the league-average constants
    when no pbp table is loaded."""
    key = "+".join(str(y) for y in _normalize_seasons(season))
    if key in _BASELINE_MEMO:
        return _BASELINE_MEMO[key]

    if key not in _MEMO:
        _MEMO[key] = build_clutch_table(season)
    table = _MEMO[key]

    base: dict[str, dict] = {}
    for state in _STATES:
        cells = [tc.splits[state] for tc in table.values()
                 if state in tc.splits and tc.splits[state].n_plays > 0]
        if not cells:
            fb = _LEAGUE_FALLBACK[state]
            base[state] = {**fb, "n_teams": 0, "n_plays": 0}
            continue
        tot_plays = sum(c.n_plays for c in cells)
        tot_games = sum(c.n_games for c in cells) or 1
        # EPA + score_rate: weight by plays. points_per_game: weight by games.
        epa = sum(c.epa_per_play * c.n_plays for c in cells) / tot_plays
        scr = sum(c.score_rate * c.n_plays for c in cells) / tot_plays
        ppg = sum(c.points_per_game * c.n_games for c in cells) / tot_games
        cb_cells = [c for c in cells if c.comeback_rate is not None]
        cb = (sum(c.comeback_rate * c.n_games for c in cb_cells)
              / (sum(c.n_games for c in cb_cells) or 1)) if cb_cells else None
        base[state] = {
            "epa_per_play": round(epa, 3),
            "points_per_game": round(ppg, 2),
            "score_rate": round(scr, 3),
            "comeback_rate": (round(cb, 3) if cb is not None else None),
            "n_teams": len(cells),
            "n_plays": tot_plays,
        }
    _BASELINE_MEMO[key] = base
    return base


def home_trailing_vs_rest(team: str, season=None) -> dict:
    """The exact comparison KC asked for: this team's Q4 profile when HOME &
    TRAILING, against the other three game states. Returns a dict ready to
    render as a table + a one-line human read."""
    tc = get_clutch(team, season)
    focus = tc.get("home_trailing")
    rows = {s: tc.get(s) for s in _STATES}

    # Reference point 1 (primary): the LEAGUE average for home_trailing — how
    # this team compares to a typical team in the exact same spot.
    # Reference point 2 (secondary color): this team's own other three states.
    base = league_baseline(season)
    lg = base.get("home_trailing", _LEAGUE_FALLBACK["home_trailing"])
    lg_epa = lg["epa_per_play"]
    lg_cb = lg.get("comeback_rate")

    others = [rows[s].epa_per_play for s in _STATES if s != "home_trailing"]
    avg_other = sum(others) / len(others) if others else 0.0
    vs_league = focus.epa_per_play - lg_epa

    if focus.n_plays == 0:
        read = f"{team}: no Q4 home-trailing sample — showing league averages."
    else:
        if vs_league >= 0.05:
            verdict = "ABOVE the league"
        elif vs_league <= -0.05:
            verdict = "BELOW the league"
        else:
            verdict = "about league-average"
        cb_txt = ""
        if focus.comeback_rate is not None and lg_cb is not None:
            cb_txt = (f"; comes back {_pct(focus.comeback_rate)} of the time "
                      f"(league {_pct(lg_cb)})")
        elif focus.comeback_rate is not None:
            cb_txt = f"; comeback rate {_pct(focus.comeback_rate)}"
        read = (f"{team} is {verdict} when home & down — Q4 EPA/play "
                f"{focus.epa_per_play:+.2f} vs league {lg_epa:+.2f} "
                f"(own other states {avg_other:+.2f}){cb_txt}.")

    return {
        "team": (team or "").upper(),
        "source": tc.source,
        "focus_state": "home_trailing",
        "states": {s: asdict(rows[s]) for s in _STATES},
        "league_baseline": base,
        "read": read,
    }


def _state_delta_vs_league(team: str, state: str, season) -> Optional[float]:
    """How far a team's EPA/play in one Q4 state sits above/below the league
    average for that same state. Positive = better than a typical team in that
    exact spot. None if the team has no real sample there."""
    tc = get_clutch(team, season)
    cell = tc.get(state)
    if cell.n_plays == 0:
        return None
    lg = league_baseline(season).get(state, _LEAGUE_FALLBACK[state])
    return round(cell.epa_per_play - lg["epa_per_play"], 3)


def power_play_signal(home: str, away: str, favorite: str,
                      edge_fav: float, season=None,
                      strong_delta: float = 0.06) -> dict:
    """Combine the situational Q4 clutch read with the model-vs-market edge to
    flag a POWER PLAY — a bet where an INDEPENDENT game-script signal agrees
    with (and reinforces) the market edge.

    Reasoning (game script, not player value):
      • The FAVORITE is expected to be protecting a lead late → grade its
        "leading at its venue" Q4 clutch (can it hold?).
      • The UNDERDOG is expected to be chasing late → grade its "trailing at its
        venue" Q4 clutch (can it claw back / cover?).
      • The market `edge_fav` says which side the model already likes
        (positive = favorite, negative = underdog).

    A POWER PLAY fires when the clutch evidence points the SAME way as the edge:
      - edge on FAVORITE  AND  (fav holds leads well  OR  dog folds when chasing)
      - edge on UNDERDOG  AND  (dog claws back well    OR  fav blows leads)

    Returns {power_play: bool, side: 'FAVORITE'|'UNDERDOG'|'', strength:
    'STRONG'|'LEAN'|'', note: str, detail: {...}}. Never raises; degrades to a
    no-signal result when clutch data is missing.
    """
    home_u, away_u, fav_u = (home or "").upper(), (away or "").upper(), (favorite or "").upper()
    dog_u = away_u if fav_u == home_u else home_u

    fav_home = (fav_u == home_u)
    fav_lead_state = "home_leading" if fav_home else "away_leading"
    dog_trail_state = "home_trailing" if not fav_home else "away_trailing"

    fav_hold = _state_delta_vs_league(fav_u, fav_lead_state, season)   # +good at holding
    dog_claw = _state_delta_vs_league(dog_u, dog_trail_state, season)  # +good at chasing

    detail = {
        "favorite": fav_u, "underdog": dog_u, "edge_fav": round(edge_fav, 3),
        "fav_lead_state": fav_lead_state, "fav_hold_vs_league": fav_hold,
        "dog_trail_state": dog_trail_state, "dog_claw_vs_league": dog_claw,
    }
    result = {"power_play": False, "side": "", "strength": "", "note": "",
              "detail": detail}

    # need a real market edge AND at least one clutch reading to say anything
    if abs(edge_fav) < 0.05 or (fav_hold is None and dog_claw is None):
        return result

    if edge_fav > 0:
        # model likes the FAVORITE — confirm if fav holds leads or dog folds
        confirms = []
        if fav_hold is not None and fav_hold >= strong_delta:
            confirms.append(f"{fav_u} holds Q4 leads {fav_hold:+.2f} vs league ({fav_lead_state})")
        if dog_claw is not None and dog_claw <= -strong_delta:
            confirms.append(f"{dog_u} folds when chasing {dog_claw:+.2f} vs league ({dog_trail_state})")
        if confirms:
            result.update(power_play=True, side="FAVORITE",
                          strength="STRONG" if len(confirms) == 2 else "LEAN",
                          note="POWER PLAY on " + fav_u + " — market edge + " + "; ".join(confirms))
    else:
        # model likes the UNDERDOG — confirm if dog claws back or fav blows leads
        confirms = []
        if dog_claw is not None and dog_claw >= strong_delta:
            confirms.append(f"{dog_u} claws back {dog_claw:+.2f} vs league ({dog_trail_state})")
        if fav_hold is not None and fav_hold <= -strong_delta:
            confirms.append(f"{fav_u} blows Q4 leads {fav_hold:+.2f} vs league ({fav_lead_state})")
        if confirms:
            result.update(power_play=True, side="UNDERDOG",
                          strength="STRONG" if len(confirms) == 2 else "LEAN",
                          note="POWER PLAY on " + dog_u + " — market edge + " + "; ".join(confirms))

    return result


def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v*100:.0f}%"


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _print_team(team: str, season) -> None:
    r = home_trailing_vs_rest(team, season)
    print(f"\n== {r['team']}  (source={r['source']}) ==")
    hdr = f"{'state':<15}{'EPA/play':>9}{'Q4 pts/g':>10}{'score%':>8}{'cmbk%':>7}{'n_pl':>6}{'n_gm':>5}"
    print(hdr)
    print("-" * len(hdr))
    for s in _STATES:
        d = r["states"][s]
        cb = "  n/a" if d["comeback_rate"] is None else f"{d['comeback_rate']*100:5.0f}"
        print(f"{s:<15}{d['epa_per_play']:>9.3f}{d['points_per_game']:>10.2f}"
              f"{d['score_rate']*100:>7.0f}%{cb:>7}{d['n_plays']:>6}{d['n_games']:>5}")
    # league baseline row for the focus state
    lg = r["league_baseline"]["home_trailing"]
    lcb = "  n/a" if lg["comeback_rate"] is None else f"{lg['comeback_rate']*100:5.0f}"
    print(f"{'LEAGUE(h_trail)':<15}{lg['epa_per_play']:>9.3f}{lg['points_per_game']:>10.2f}"
          f"{lg['score_rate']*100:>7.0f}%{lcb:>7}{lg['n_plays']:>6}{lg.get('n_teams',0):>5}")
    print(f"read: {r['read']}")


if __name__ == "__main__":
    # Pass one or more seasons on the CLI; default pools 2024+2025 to smooth
    # the small single-season samples.
    # Pass one or more seasons on the CLI; default is the AUTO rolling window
    # (current season once it has data + recency-weighted priors).
    _seasons = [int(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else None
    print(f"clutch_split self-test (seasons={_seasons or 'auto: '+str(_auto_window())})")
    for _t in ("KC", "DET", "BAL", "SF", "DAL"):
        _print_team(_t, _seasons)
