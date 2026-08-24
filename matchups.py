"""
Matchups & Strength-of-Schedule module — the "art + science" layer.

VORP (engine.py) answers "who is good in a vacuum." This module answers
"who is SET UP to smash" by scoring a player's real schedule against how
soft each opponent's pass defense is, and evaluates correlated QB-WR/TE
STACKS (e.g. Stafford + Adams) where a TD scores on both roster spots.

DATA POSTURE (important): the tables below are a SEEDED SNAPSHOT
(2026 schedules + 2025 pass-defense-allowed). They are the fallback so the
app always runs, but `load_pass_defense()` / `load_schedule()` are the seams
to swap in a live pull (nfl_data_py, ESPN, PFR) — rankings and schedules
should update off live data, never be trusted as frozen truth. All numbers
are overridable via a JSON file in data/.

Fantasy-relevant weeks: regular season 1-17; fantasy playoffs typically
weeks 15-17 (redraft) — weighted extra because they decide championships.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# ---------------------------------------------------------------------------
# Seeded pass-defense softness: 2025 passing TDs allowed per team (NFL.com).
# Higher = softer through the air = better for opposing QB/WR/TE.
# League avg ~26-27. This is the "how many multi-pass-TD defenses" signal.
# ---------------------------------------------------------------------------
PASS_TD_ALLOWED_2025 = {
    "ARI": 31, "CIN": 33, "DAL": 35, "DEN": 18, "LAR": 26, "NO": 25,
    "KC": 29, "WAS": 33, "BAL": 23, "NYG": 28, "PHI": 24, "BUF": 27,
    "LV": 30, "LAC": 25, "GB": 24, "SF": 26, "SEA": 27, "TB": 29,
    # remaining teams default to league-average when absent
}
_LEAGUE_AVG_PASS_TD = 26.5

# ---------------------------------------------------------------------------
# Seeded 2026 schedules (opponent by week). Home/away not needed for pass-D
# softness. Empty week = BYE. Source: team season pages (Wikipedia/NFL.com).
# Only teams we've loaded are needed; extend or load live for the rest.
# ---------------------------------------------------------------------------
SCHEDULE_2026 = {
    "LAR": {  # Los Angeles Rams — Stafford, Adams, Nacua, Kyren
        1: "SF", 2: "NYG", 3: "DEN", 4: "PHI", 5: "BUF", 6: "ARI",
        7: "LV", 8: "LAC", 9: "WAS", 10: "ARI", 11: None,  # bye
        12: "GB", 13: "KC", 14: "SF", 15: "DAL", 16: "SEA", 17: "TB", 18: "SEA",
    },
}

# Which players anchor a stack, and their team (seed; extend/load live).
KNOWN_TEAM = {
    "Matthew Stafford": "LAR", "Davante Adams": "LAR", "Puka Nacua": "LAR",
    "Kyren Williams": "LAR",
}

FANTASY_PLAYOFF_WEEKS = (15, 16, 17)


# ---------------------------------------------------------------------------
# loaders (the live-swap seam)
# ---------------------------------------------------------------------------
def load_pass_defense() -> dict[str, float]:
    override = os.path.join(_DATA_DIR, "pass_defense.json")
    if os.path.exists(override):
        with open(override) as f:
            return {k: float(v) for k, v in json.load(f).items()}
    return dict(PASS_TD_ALLOWED_2025)


def load_schedule() -> dict[str, dict[int, Optional[str]]]:
    override = os.path.join(_DATA_DIR, "schedule.json")
    if os.path.exists(override):
        with open(override) as f:
            raw = json.load(f)
        return {tm: {int(w): opp for w, opp in wks.items()} for tm, wks in raw.items()}
    return SCHEDULE_2026


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
@dataclass
class WeekMatchup:
    week: int
    opponent: Optional[str]
    pass_td_allowed: Optional[float]
    softness: Optional[float]      # opp pass_td_allowed - league_avg (+ = soft)
    is_playoff_week: bool


@dataclass
class ScheduleReport:
    team: str
    weeks: list[WeekMatchup]
    soft_weeks: int                # count of clearly-soft pass-D matchups
    playoff_softness: float        # avg softness over fantasy playoff weeks
    season_softness: float         # avg softness across all played weeks
    grade: str


def schedule_report(team: str, soft_threshold: float = 3.0) -> Optional[ScheduleReport]:
    """Score a team's full pass-defense schedule. `soft_threshold` = how many
    pass-TDs-above-average counts as a genuinely soft week."""
    sched = load_schedule().get(team)
    if not sched:
        return None
    pd = load_pass_defense()

    weeks: list[WeekMatchup] = []
    softs: list[float] = []
    playoff_softs: list[float] = []
    soft_count = 0
    for wk in sorted(sched):
        opp = sched[wk]
        if opp is None:  # bye
            weeks.append(WeekMatchup(wk, None, None, None, wk in FANTASY_PLAYOFF_WEEKS))
            continue
        allowed = pd.get(opp, _LEAGUE_AVG_PASS_TD)
        soft = allowed - _LEAGUE_AVG_PASS_TD
        is_po = wk in FANTASY_PLAYOFF_WEEKS
        weeks.append(WeekMatchup(wk, opp, allowed, round(soft, 1), is_po))
        softs.append(soft)
        if is_po:
            playoff_softs.append(soft)
        if soft >= soft_threshold:
            soft_count += 1

    season_soft = round(sum(softs) / len(softs), 1) if softs else 0.0
    po_soft = round(sum(playoff_softs) / len(playoff_softs), 1) if playoff_softs else 0.0
    return ScheduleReport(
        team=team, weeks=weeks, soft_weeks=soft_count,
        playoff_softness=po_soft, season_softness=season_soft,
        grade=_grade(season_soft, po_soft, soft_count),
    )


def _grade(season_soft: float, po_soft: float, soft_count: int) -> str:
    # Weight the fantasy playoffs heavily — that's what wins championships.
    score = season_soft + 1.5 * po_soft + 0.6 * soft_count
    if score >= 8:
        return "A  (schedule smashes — draft the stack)"
    if score >= 4:
        return "B  (favorable, playoff weeks help)"
    if score >= 0:
        return "C  (neutral schedule)"
    return "D  (tough pass-D slate — fade the stack)"


@dataclass
class StackEval:
    qb: str
    pass_catcher: str
    team: str
    report: ScheduleReport
    correlation_note: str = (
        "Correlated stack: a QB->receiver TD scores on BOTH roster spots, so "
        "upside compounds on soft-defense weeks. Contrarian if the QB is a "
        "late-round value nobody else targets."
    )


def evaluate_stack(qb: str, pass_catcher: str) -> Optional[StackEval]:
    """Evaluate a QB + WR/TE stack (e.g. Stafford + Adams). Both must be on the
    same team in KNOWN_TEAM (or a loaded team map)."""
    tq, tc = KNOWN_TEAM.get(qb), KNOWN_TEAM.get(pass_catcher)
    if not tq or tq != tc:
        return None
    rpt = schedule_report(tq)
    if not rpt:
        return None
    return StackEval(qb=qb, pass_catcher=pass_catcher, team=tq, report=rpt)
