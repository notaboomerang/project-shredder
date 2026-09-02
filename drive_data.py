"""
Drive / possession data — the discrete-football layer under the live win-prob model.

The base win-prob engine (win_prob.py) treats time as a smooth fraction. Football
is not smooth: it is a sequence of DISCRETE possessions. What matters late in a
game is not "how many seconds are left" but "how many scoring drives does each
team realistically still get." This module produces that number.

Three pieces, cleanly separated:

  1. SEASON AGGREGATES  — per team, average drive length (seconds) and drives per
     game, SPLIT by game script (leading vs trailing). Sourced from nfl_data_py
     play-by-play when installed; otherwise a shipped league-average fallback so
     the feature always works (off-day, fresh install, no network).

  2. LIVE DRIVE / POSSESSION — parsed from ESPN's free game-summary endpoint:
     who currently has the ball, the current drive's elapsed time, and the list
     of completed drives. Degrades gracefully (None / []) on any failure, exactly
     like live_games.py.

  3. POSSESSIONS-REMAINING CALCULATOR — the bridge. It walks the remaining clock,
     alternating each side's OWN average drive length (the leader milks clock with
     its leading-state pace; the trailer hurries with its trailing-state pace),
     starting with whoever currently has the ball, and counts how many drives each
     team fits before regulation ends.

Nothing here is betting advice. It is a more faithful model of opportunity.

SELF-TEST: run `python drive_data.py <espn_event_id>` on a machine that can reach
ESPN to print the parsed live drives + possessions-left, so the field names can be
verified against a real game. (This sandbox is blocked from ESPN with a 403, so the
live parse is written defensively and confirmed by you locally.)
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import espn_http as _http

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"

_SECONDS_TOTAL = 3600.0
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_CACHE = os.path.join(_DATA_DIR, "drive_aggregates.json")

# League-average fallback (seconds per drive, drives per game), split by script.
# These are reasonable NFL-wide values used ONLY when nfl_data_py is unavailable
# or a specific team has no data. Trailing = hurry-up (shorter); leading = milk
# clock (longer). Neutral is the blended baseline.
_LEAGUE_FALLBACK = {
    "leading":  {"drive_seconds": 175.0, "drives_per_game": 11.0},
    "trailing": {"drive_seconds": 135.0, "drives_per_game": 12.5},
    "neutral":  {"drive_seconds": 155.0, "drives_per_game": 11.8},
}


# ---------------------------------------------------------------------------
# 1. Season aggregates
# ---------------------------------------------------------------------------

@dataclass
class TeamDrivePace:
    """A team's drive tempo, split by game script. Seconds are per drive."""
    team: str
    lead_drive_seconds: float
    lead_drives_per_game: float
    trail_drive_seconds: float
    trail_drives_per_game: float
    source: str = "fallback"        # "pbp" | "cache" | "fallback"

    def drive_seconds(self, script: str) -> float:
        """script in {'leading','trailing','neutral'}."""
        if script == "leading":
            return self.lead_drive_seconds
        if script == "trailing":
            return self.trail_drive_seconds
        return (self.lead_drive_seconds + self.trail_drive_seconds) / 2.0


def _fallback_pace(team: str) -> TeamDrivePace:
    lead = _LEAGUE_FALLBACK["leading"]
    trail = _LEAGUE_FALLBACK["trailing"]
    return TeamDrivePace(
        team=team,
        lead_drive_seconds=lead["drive_seconds"],
        lead_drives_per_game=lead["drives_per_game"],
        trail_drive_seconds=trail["drive_seconds"],
        trail_drives_per_game=trail["drives_per_game"],
        source="fallback",
    )


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


def _mmss_to_seconds(v) -> Optional[float]:
    """Parse a 'MM:SS' (or 'M:SS') clock string to seconds; None if unparseable."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        parts = str(v).strip().split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 1 and parts[0] != "":
            return float(parts[0])
    except Exception:
        return None
    return None


def build_season_aggregates(season: Optional[int] = None,
                            use_cache: bool = True) -> dict[str, TeamDrivePace]:
    """Per-team drive pace split by leading/trailing for a season.

    Tries nfl_data_py play-by-play; falls back to a cached build, then to the
    league-average constants. Always returns a dict keyed by team abbrev — never
    raises. Missing teams are filled from the fallback on demand via get_pace().
    """
    # 1) cache
    if use_cache:
        cached = _load_cache()
        key = str(season or cached.get("_season") or "")
        teams = cached.get("teams") if isinstance(cached, dict) else None
        if teams and (not season or str(cached.get("_season")) == str(season)):
            out = {}
            for t, d in teams.items():
                out[t] = TeamDrivePace(
                    team=t,
                    lead_drive_seconds=d["lead_drive_seconds"],
                    lead_drives_per_game=d["lead_drives_per_game"],
                    trail_drive_seconds=d["trail_drive_seconds"],
                    trail_drives_per_game=d["trail_drives_per_game"],
                    source="cache",
                )
            if out:
                return out

    # 2) nfl_data_py play-by-play. If the requested/current season has no data
    #    yet (preseason or Week 1 -> 404), fall back to the prior completed
    #    season, which is far better than flat league averages.
    try:
        import nfl_data_py as nfl  # type: ignore
        import pandas as pd  # noqa: F401

        base = season or _default_season()
        for yr in ([base] if season else [base, base - 1]):
            try:
                pbp = nfl.import_pbp_data([yr], downcast=True, cache=False)
            except Exception:
                continue  # e.g. HTTP 404 for a not-yet-played season
            agg = _aggregate_pbp(pbp)
            if agg:
                _save_cache({"_season": yr,
                             "teams": {t: _pace_to_dict(p) for t, p in agg.items()}})
                return agg
    except Exception:
        # any failure (package missing, network, schema drift) -> fallback below
        pass

    # 3) empty dict; callers use get_pace() which fills from fallback
    return {}


def _default_season() -> int:
    import datetime as _dt
    now = _dt.date.today()
    # NFL season is labeled by the year it starts (Sep). Jan-Jul -> prior year.
    return now.year if now.month >= 8 else now.year - 1


def _aggregate_pbp(pbp) -> dict[str, TeamDrivePace]:
    """Aggregate nflverse play-by-play into per-team leading/trailing drive pace.

    Uses one row per (game, drive) so drive time isn't double-counted across plays.
    'script' for a drive is decided by the possessing team's score margin at the
    START of the drive: >0 leading, <0 trailing, ==0 neutral (folded into both).
    """
    import pandas as pd

    needed = {"game_id", "posteam", "drive", "drive_time_of_possession",
              "posteam_score", "defteam_score"}
    cols = set(pbp.columns)
    if not needed.issubset(cols):
        # schema drift — bail to fallback
        return {}

    df = pbp.dropna(subset=["posteam", "drive"]).copy()
    # collapse to one row per drive (first play carries start-of-drive context)
    firsts = df.sort_values(["game_id", "drive"]).groupby(
        ["game_id", "drive"], as_index=False).first()

    firsts["top_sec"] = firsts["drive_time_of_possession"].map(_mmss_to_seconds)
    firsts = firsts.dropna(subset=["top_sec"])
    firsts["margin"] = firsts["posteam_score"] - firsts["defteam_score"]

    out: dict[str, TeamDrivePace] = {}
    for team, g in firsts.groupby("posteam"):
        n_games = g["game_id"].nunique() or 1
        lead = g[g["margin"] > 0]
        trail = g[g["margin"] < 0]
        neutral = g[g["margin"] == 0]
        # neutral drives inform both splits so short samples still get a number
        lead_pool = pd.concat([lead, neutral]) if len(lead) else neutral
        trail_pool = pd.concat([trail, neutral]) if len(trail) else neutral

        fb = _LEAGUE_FALLBACK
        out[team] = TeamDrivePace(
            team=team,
            lead_drive_seconds=(float(lead_pool["top_sec"].mean())
                                if len(lead_pool) else fb["leading"]["drive_seconds"]),
            lead_drives_per_game=(len(lead) / n_games
                                  if len(lead) else fb["leading"]["drives_per_game"]),
            trail_drive_seconds=(float(trail_pool["top_sec"].mean())
                                 if len(trail_pool) else fb["trailing"]["drive_seconds"]),
            trail_drives_per_game=(len(trail) / n_games
                                   if len(trail) else fb["trailing"]["drives_per_game"]),
            source="pbp",
        )
    return out


def _pace_to_dict(p: TeamDrivePace) -> dict:
    return {
        "lead_drive_seconds": p.lead_drive_seconds,
        "lead_drives_per_game": p.lead_drives_per_game,
        "trail_drive_seconds": p.trail_drive_seconds,
        "trail_drives_per_game": p.trail_drives_per_game,
    }


_AGG_MEMO: dict[str, dict[str, TeamDrivePace]] = {}


def get_pace(team: str, season: Optional[int] = None) -> TeamDrivePace:
    """Team drive pace with fallback. Never raises. Caches the season table."""
    key = str(season or "")
    if key not in _AGG_MEMO:
        _AGG_MEMO[key] = build_season_aggregates(season)
    table = _AGG_MEMO[key]
    return table.get(team) or _fallback_pace(team)


# ---------------------------------------------------------------------------
# 2. Live drive / possession parse (ESPN game-summary endpoint)
# ---------------------------------------------------------------------------

@dataclass
class LiveDrive:
    team: str                 # possessing team abbrev
    elapsed_seconds: Optional[float]
    plays: Optional[int]
    result: str               # "Punt" / "Touchdown" / "Field Goal" ...
    is_score: bool


@dataclass
class LivePossession:
    """Snapshot of who has the ball right now + completed-drive history."""
    event_id: str
    possessing_team: Optional[str] = None    # team abbrev with the ball, or None
    current_drive_elapsed: Optional[float] = None
    drives: list[LiveDrive] = field(default_factory=list)
    ok: bool = False                          # True if we parsed anything real


def _summary_json(event_id: str) -> dict:
    # Routes through espn_http (curl_cffi browser-impersonation) to defeat the
    # Akamai TLS-fingerprint 403 that blocks plain requests.
    return _http.get_json(_SUMMARY, params={"event": event_id}, timeout=12)


def fetch_live_possession(event_id: str, data: Optional[dict] = None
                          ) -> LivePossession:
    """Parse ESPN's summary endpoint for possession + completed drives.

    ESPN summary shape (validated shapes across seasons):
      data['drives']['previous'] -> list of completed drives, each with
          'team': {'abbreviation': 'KC'}, 'timeElapsed': {'displayValue': 'MM:SS'},
          'offensivePlays': int, 'displayResult'/'result': str, 'isScore': bool
      data['drives']['current']  -> the in-progress drive (same shape)
      data['header']...           -> also carries a 'possession' team id in some
          shapes; we prefer drives.current.team when present.

    Written defensively: any missing key degrades to None/[]. Returns ok=False if
    nothing parseable (so callers can fall back to the clock-only model).
    """
    lp = LivePossession(event_id=str(event_id))
    d = data if data is not None else _summary_json(event_id)
    if not d:
        return lp

    drives_obj = d.get("drives") or {}
    prev = drives_obj.get("previous") or []
    cur = drives_obj.get("current")

    def _team_abbr(node) -> str:
        t = (node or {}).get("team") or {}
        return t.get("abbreviation") or t.get("abbrev") or ""

    def _elapsed(node) -> Optional[float]:
        te = (node or {}).get("timeElapsed") or {}
        # sometimes a dict {'displayValue':'MM:SS'} sometimes a bare string
        if isinstance(te, dict):
            return _mmss_to_seconds(te.get("displayValue"))
        return _mmss_to_seconds(te)

    for dr in prev:
        try:
            lp.drives.append(LiveDrive(
                team=_team_abbr(dr),
                elapsed_seconds=_elapsed(dr),
                plays=(dr.get("offensivePlays") or dr.get("plays")
                       if isinstance(dr.get("plays"), int) else dr.get("offensivePlays")),
                result=str(dr.get("displayResult") or dr.get("result") or ""),
                is_score=bool(dr.get("isScore", False)),
            ))
        except Exception:
            continue

    if cur:
        lp.possessing_team = _team_abbr(cur) or None
        lp.current_drive_elapsed = _elapsed(cur)

    # secondary source for possession: situation/header (id -> abbrev needs map)
    if lp.possessing_team is None:
        sit = ((d.get("situation") or {}) if isinstance(d.get("situation"), dict)
               else {})
        # 'lastPlay'/'possession' sometimes carry a team id; abbrev preferred, skip
        # id-only forms here to avoid a wrong guess — clock-only fallback is fine.
        pass

    lp.ok = bool(lp.drives) or lp.possessing_team is not None
    return lp


# ---------------------------------------------------------------------------
# 3. Possessions-remaining calculator (the bridge)
# ---------------------------------------------------------------------------

@dataclass
class PossessionsLeft:
    fav_drives_left: float
    dog_drives_left: float
    total_drives_left: float
    first_possession: str        # "fav" | "dog"
    note: str = ""


def _seconds_left(quarter: int, clock: str) -> float:
    """Seconds remaining in regulation from quarter + 'MM:SS' clock."""
    if not quarter:
        return _SECONDS_TOTAL
    q_left = _mmss_to_seconds(clock) or 0.0
    quarters_after = max(0, 4 - quarter)
    return min(_SECONDS_TOTAL, quarters_after * 900.0 + q_left)


def possessions_remaining(seconds_left: float,
                          fav_drive_seconds: float,
                          dog_drive_seconds: float,
                          possessing: str) -> PossessionsLeft:
    """Walk the remaining clock, alternating drives starting with `possessing`.

    `possessing` in {'fav','dog'} = who has the ball NOW. Each side consumes its
    OWN average drive length off the clock; we count how many full (or partial)
    drives each team fits. A ball-control leader with long drives eats the clock
    and shrinks the trailer's drive count — exactly the effect we want.

    Returns fractional drives (a half-finished drive counts as its fractional
    share of clock) so the downstream model degrades smoothly.
    """
    fav_drive_seconds = max(20.0, float(fav_drive_seconds))
    dog_drive_seconds = max(20.0, float(dog_drive_seconds))
    rem = max(0.0, float(seconds_left))
    turn = possessing if possessing in ("fav", "dog") else "fav"
    first = turn

    fav_d = dog_d = 0.0
    # cap iterations defensively (a game can't have hundreds of drives)
    for _ in range(60):
        if rem <= 0:
            break
        dur = fav_drive_seconds if turn == "fav" else dog_drive_seconds
        used = min(dur, rem)
        share = used / dur                      # 1.0 full drive, <1 if clock ran out
        if turn == "fav":
            fav_d += share
        else:
            dog_d += share
        rem -= used
        turn = "dog" if turn == "fav" else "fav"

    note = (f"{first} ball · fav≈{fav_d:.1f} / dog≈{dog_d:.1f} drives left")
    return PossessionsLeft(
        fav_drives_left=round(fav_d, 2),
        dog_drives_left=round(dog_d, 2),
        total_drives_left=round(fav_d + dog_d, 2),
        first_possession=first,
        note=note,
    )


def drives_left_for_game(quarter: int, clock: str,
                         fav_team: str, dog_team: str,
                         fav_is_leading: bool,
                         possessing: Optional[str] = None,
                         season: Optional[int] = None) -> PossessionsLeft:
    """High-level helper: pick the right script-split pace for each side and
    compute drives remaining.

    The LEADER uses its leading-state (clock-milking) drive length; the TRAILER
    uses its trailing-state (hurry-up) drive length — the realistic pairing.
    `possessing` = 'fav'/'dog'/None; None defaults to 'fav'.
    """
    fav_pace = get_pace(fav_team, season)
    dog_pace = get_pace(dog_team, season)

    if fav_is_leading:
        fav_secs = fav_pace.drive_seconds("leading")
        dog_secs = dog_pace.drive_seconds("trailing")
    else:
        fav_secs = fav_pace.drive_seconds("trailing")
        dog_secs = dog_pace.drive_seconds("leading")

    sec_left = _seconds_left(quarter, clock)
    return possessions_remaining(sec_left, fav_secs, dog_secs,
                                 possessing or "fav")


# ---------------------------------------------------------------------------
# 4. Self-test (run locally, on a machine that can reach ESPN)
# ---------------------------------------------------------------------------

def _selftest(event_id: str) -> None:
    print(f"== drive_data self-test for event {event_id} ==")
    print(f"http backend: {_http.backend()} (available={_http.available()})")

    lp = fetch_live_possession(event_id)
    print(f"parse ok: {lp.ok}")
    print(f"possessing team: {lp.possessing_team}")
    print(f"current drive elapsed (s): {lp.current_drive_elapsed}")
    print(f"completed drives parsed: {len(lp.drives)}")
    for dr in lp.drives[:6]:
        print(f"   {dr.team:>4}  {dr.elapsed_seconds}s  plays={dr.plays}  "
              f"{dr.result}  score={dr.is_score}")

    # demo of the calculator with league-average paces (no live pace needed)
    demo = possessions_remaining(seconds_left=900.0,          # ~Q3 start
                                 fav_drive_seconds=175.0,     # leader milks
                                 dog_drive_seconds=135.0,     # trailer hurries
                                 possessing="dog")
    print("\ncalculator demo (15:00 left, dog has ball):")
    print(f"   {demo.note}")

    fb = _fallback_pace("KC")
    print(f"\nfallback pace sample (KC): lead={fb.lead_drive_seconds}s "
          f"trail={fb.trail_drive_seconds}s (source={fb.source})")


if __name__ == "__main__":
    _ev = sys.argv[1] if len(sys.argv) > 1 else ""
    if not _ev:
        print("usage: python drive_data.py <espn_event_id>")
        print("(find an id in the scoreboard JSON on a machine that can reach ESPN)")
        # still show the offline calculator demo
        _selftest("401671789")
    else:
        _selftest(_ev)
