"""
Divisions — deterministic NFL division map + a division-game check.

Purely factual: two teams are in a "division game" iff they share a division.
No scoring, no narrative, no rivalry folklore — just a true/false fact derived
from the fixed division alignment. Surfaces as a context tag ("division game")
on matchup / eruption rows so you SEE it; it does not move any projection.

(If we later decide division games run tighter/lower — which the evidence leans
toward — the damper would live in the consuming module, not here. This file
stays a pure lookup.)
"""
from __future__ import annotations

# Team abbreviations use the nflverse/standard set the rest of the app uses.
_DIVISIONS: dict[str, tuple[str, ...]] = {
    "AFC East":  ("BUF", "MIA", "NE", "NYJ"),
    "AFC North": ("BAL", "CIN", "CLE", "PIT"),
    "AFC South": ("HOU", "IND", "JAX", "TEN"),
    "AFC West":  ("DEN", "KC", "LV", "LAC"),
    "NFC East":  ("DAL", "NYG", "PHI", "WAS"),
    "NFC North": ("CHI", "DET", "GB", "MIN"),
    "NFC South": ("ATL", "CAR", "NO", "TB"),
    "NFC West":  ("ARI", "LAR", "SF", "SEA"),
}

# team -> division name (built once)
_TEAM_DIV: dict[str, str] = {
    t: div for div, teams in _DIVISIONS.items() for t in teams
}


def division_of(team: str) -> str:
    """Division name for a team abbrev ('AFC East'), or '' if unknown."""
    return _TEAM_DIV.get((team or "").upper(), "")


def same_division(a: str, b: str) -> bool:
    """True iff both teams share a division (and both are known)."""
    da = division_of(a)
    db = division_of(b)
    return bool(da) and da == db


def is_division_game(team: str, opponent: str) -> bool:
    """Alias for same_division — reads naturally at call sites."""
    return same_division(team, opponent)


def division_tag(team: str, opponent: str) -> str:
    """A short context label, or '' when it's not a division game.
    e.g. 'division game (NFC East)'. Purely informational."""
    if same_division(team, opponent):
        return f"division game ({division_of(team)})"
    return ""
