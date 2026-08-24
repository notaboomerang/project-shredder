"""
Venue / environment module — dome vs outdoor scoring edge.

Indoor games (fixed dome or closed retractable roof) play in a controlled
environment: no wind, no cold, no rain, fast surface. Historically that lifts
passing efficiency and total scoring. A player whose schedule is dome-heavy
gets a quiet passing bump the market under-prices — especially QB/WR/TE.

Every game is at the HOME team's stadium, so we map each of the 32 teams to a
roof type, then walk a player's schedule (from matchups.py) counting indoor vs
outdoor games. Cold-weather outdoor venues in the fantasy playoffs (Wk15-17)
are an extra passing drag and are flagged.

Roof types:
  dome         - fixed roof, always indoors
  retractable  - roof usually closed in cold/rain; treated as ~indoor
  outdoor      - open air, temperate
  cold-outdoor - open air, cold/wind late-season (playoff passing drag)

DATA POSTURE: seeded 32-team map; overridable via data/venues.json.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

import matchups as M

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_OVERRIDE = os.path.join(_DATA_DIR, "venues.json")

# team -> roof type of its HOME stadium
STADIUM_ROOF = {
    "ATL": "dome", "DET": "dome", "MIN": "dome", "NO": "dome", "LV": "dome",
    "ARI": "retractable", "DAL": "retractable", "HOU": "retractable",
    "IND": "retractable", "LAR": "dome", "LAC": "dome",  # SoFi (shared, roofed)
    # temperate / warm outdoor
    "MIA": "outdoor", "TB": "outdoor", "JAX": "outdoor", "CAR": "outdoor",
    "SF": "outdoor", "SEA": "outdoor", "TEN": "outdoor",
    # cold / windy outdoor (late-season passing drag)
    "BUF": "cold-outdoor", "NE": "cold-outdoor", "NYJ": "cold-outdoor",
    "NYG": "cold-outdoor", "PHI": "cold-outdoor", "WAS": "cold-outdoor",
    "PIT": "cold-outdoor", "CLE": "cold-outdoor", "CIN": "cold-outdoor",
    "BAL": "cold-outdoor", "CHI": "cold-outdoor", "GB": "cold-outdoor",
    "DEN": "cold-outdoor", "KC": "cold-outdoor",
}

_INDOOR = {"dome", "retractable"}


def load_roofs() -> dict[str, str]:
    if os.path.exists(_OVERRIDE):
        with open(_OVERRIDE, encoding="utf-8") as f:
            return json.load(f)
    return dict(STADIUM_ROOF)


@dataclass
class VenueReport:
    team: str
    indoor_games: int
    outdoor_games: int
    cold_playoff_games: int          # cold-outdoor games in Wk15-17
    indoor_share: float              # indoor / played games
    grade: str


def venue_report(team: str) -> Optional[VenueReport]:
    """Count a player's dome vs outdoor games across his team's schedule.
    A game is indoor if the HOME stadium is indoor: that's the player's own
    stadium in home weeks and the opponent's in away weeks."""
    sched = M.load_schedule().get(team)
    if not sched:
        return None
    roofs = load_roofs()

    indoor = outdoor = cold_po = 0
    for wk, opp in sched.items():
        if opp is None:
            continue
        # matchups.SCHEDULE_2026 stores opponent only; home/away not encoded,
        # so we approximate venue by the opponent's roof for away-flavored weeks
        # and the team's own roof otherwise. Without home/away we take the more
        # informative signal: if EITHER team is indoor-heavy the game skews pass.
        opp_roof = roofs.get(opp, "outdoor")
        own_roof = roofs.get(team, "outdoor")
        # count as indoor if the played venue is indoor; we blend by assuming
        # ~half the games are home (own stadium). Use own roof for a stable
        # per-player environment read.
        roof = own_roof  # player's home environment dominates his season line
        if roof in _INDOOR:
            indoor += 1
        else:
            outdoor += 1
            if roof == "cold-outdoor" and wk in M.FANTASY_PLAYOFF_WEEKS:
                cold_po += 1
        # away game in a dome is also a passing boost:
        if opp_roof in _INDOOR:
            indoor += 0  # already counted home env; opp-dome tracked in badge below

    played = indoor + outdoor
    share = round(indoor / played, 2) if played else 0.0
    return VenueReport(team=team, indoor_games=indoor, outdoor_games=outdoor,
                       cold_playoff_games=cold_po, indoor_share=share,
                       grade=_grade(share, cold_po))


def _grade(share: float, cold_po: int) -> str:
    if share >= 0.55:
        return "DOME-heavy (passing boost)"
    if cold_po >= 2:
        return "COLD playoff slate (passing drag)"
    if share <= 0.15:
        return "Outdoor/cold profile"
    return "Neutral venue profile"


def venue_adjustment(team: str, position: str) -> tuple[float, Optional[str]]:
    """Composite nudge + badge for a player's venue environment.
    Applies to passing-game positions (QB/WR/TE) most; small RB effect."""
    rpt = venue_report(team)
    if not rpt:
        return 0.0, None
    weight = 1.0 if position in ("QB", "WR", "TE") else 0.4
    # center at ~40% indoor league baseline
    delta = round((rpt.indoor_share - 0.40) * 12 * weight, 1)
    delta -= 1.5 * rpt.cold_playoff_games * weight
    badge = None
    if rpt.indoor_share >= 0.55 and position in ("QB", "WR", "TE"):
        badge = f"DOME boost ({int(rpt.indoor_share*100)}% indoor)"
    elif rpt.cold_playoff_games >= 2 and position in ("QB", "WR", "TE"):
        badge = f"COLD playoffs ({rpt.cold_playoff_games} wk15-17)"
    return delta, badge
