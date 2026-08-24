"""
All-32-team 2026 schedules — unlocks venue, pass-D and rush-D softness
league-wide (matchups.py + venue.py walk these).

DATA POSTURE (read this): the LOS ANGELES RAMS (LAR) schedule is CONFIRMED from
the 2026 season page. The other 31 teams are BEST-EFFORT / partially synthetic:
each is a complete, internally-consistent 18-week slate with a bye, built to be
plausible rather than authoritative. This exists so the engine's schedule-based
edges (dome counts, pass-D softness) produce sensible numbers for every player
offline. For real accuracy, drop a data/schedule.json to override any/all teams
(matchups.load_schedule already reads that file first).

merge_into_matchups() folds every team into matchups.SCHEDULE_2026 at runtime.
"""
from __future__ import annotations

# Confirmed 2026 Rams slate (from the season page).
_LAR = {1: "SF", 2: "NYG", 3: "DEN", 4: "PHI", 5: "BUF", 6: "ARI", 7: "LV",
        8: "LAC", 9: "WAS", 10: "ARI", 11: None, 12: "GB", 13: "KC", 14: "SF",
        15: "DAL", 16: "SEA", 17: "TB", 18: "SEA"}

# Division map (for building plausible slates: everyone plays division rivals x2)
_DIV = {
    "AFCE": ["BUF", "MIA", "NE", "NYJ"], "AFCN": ["BAL", "CIN", "CLE", "PIT"],
    "AFCS": ["HOU", "IND", "JAX", "TEN"], "AFCW": ["DEN", "KC", "LV", "LAC"],
    "NFCE": ["DAL", "NYG", "PHI", "WAS"], "NFCN": ["CHI", "DET", "GB", "MIN"],
    "NFCS": ["ATL", "CAR", "NO", "TB"],   "NFCW": ["ARI", "LAR", "SF", "SEA"],
}
_TEAM_DIV = {t: d for d, ts in _DIV.items() for t in ts}
_ALL_TEAMS = [t for ts in _DIV.values() for t in ts]


def _build_plausible(team: str, bye_week: int) -> dict:
    """Construct a complete 18-week slate: division rivals home+away (6 games),
    then fill remaining weeks round-robin against non-division teams. Plausible
    and internally consistent, NOT the real slate."""
    rivals = [t for t in _DIV[_TEAM_DIV[team]] if t != team]
    others = [t for t in _ALL_TEAMS if t != team and t not in rivals]
    # opponent pool: each rival twice + enough others to reach 17 games
    opps = rivals * 2
    oi = 0
    while len(opps) < 17:
        opps.append(others[oi % len(others)])
        oi += 1
    opps = opps[:17]
    sched = {}
    gi = 0
    for wk in range(1, 19):
        if wk == bye_week:
            sched[wk] = None
        else:
            sched[wk] = opps[gi]
            gi += 1
    return sched


# assign each team a bye week spread across 5-14 (deterministic, varied)
_BYES = {t: 5 + (i % 10) for i, t in enumerate(_ALL_TEAMS)}

SCHEDULE_ALL: dict[str, dict] = {}
for _t in _ALL_TEAMS:
    if _t == "LAR":
        SCHEDULE_ALL[_t] = dict(_LAR)
    else:
        SCHEDULE_ALL[_t] = _build_plausible(_t, _BYES[_t])


def merge_into_matchups() -> int:
    """Fold all 32 team schedules into matchups.SCHEDULE_2026 (does not clobber
    an existing entry unless synthetic-filling). Returns count merged."""
    import matchups as M
    n = 0
    for team, sched in SCHEDULE_ALL.items():
        # keep matchups' own confirmed entry (LAR) if present; add the rest
        if team not in M.SCHEDULE_2026:
            M.SCHEDULE_2026[team] = dict(sched)
            n += 1
    return n
