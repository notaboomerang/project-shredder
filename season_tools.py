"""
Season Tools — the 17-week loop, not just draft day.

1. start_sit(roster, week)      — optimal weekly lineup: fill each lineup slot
                                   with the highest matchup-adjusted projection,
                                   flag close calls and clear sits.
2. evaluate_trade(give, get)    — VORP + starter-upgrade value both ways, with a
                                   plain verdict (WIN / FAIR / LOSE).

Both reuse the real engines: engine.project_points (weekly = season/17),
matchups pass-D softness, and roster construction. INFORMATIONAL — no value
inflation; the matchup tilt is a transparent, bounded adjustment shown to you.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import engine as E
import matchups as M

# how much a soft/hard pass-D matchup tilts a weekly projection (bounded)
_MATCHUP_TILT = 0.12   # +/-12% max at the extremes


def _norm_team(t: str) -> str:
    return {"WSH": "WAS", "JAC": "JAX", "LA": "LAR"}.get((t or "").upper(),
                                                         (t or "").upper())


def _weekly_points(raw, scoring: E.Scoring) -> float:
    """Season projection -> per-week baseline (17-game season)."""
    return E.project_points(raw.stats, scoring) / 17.0


def _matchup_multiplier(team: str, position: str, week: int) -> tuple[float, str]:
    """Pass-catchers/QB get tilted by the opponent's pass-D softness that week.
    RBs are left neutral here (rush-D table not seeded). Returns (mult, note)."""
    if position not in ("QB", "WR", "TE"):
        return 1.0, ""
    sched = M.load_schedule().get(_norm_team(team))
    if not sched:
        return 1.0, ""
    opp = sched.get(week)
    if not opp:
        return 1.0, "BYE"
    pd = M.load_pass_defense()
    softness = pd.get(_norm_team(opp), M._LEAGUE_AVG_PASS_TD) - M._LEAGUE_AVG_PASS_TD
    # scale softness (~ +/-8 pass TD range) into +/- tilt
    tilt = max(-_MATCHUP_TILT, min(_MATCHUP_TILT, (softness / 8.0) * _MATCHUP_TILT))
    if tilt >= 0.04:
        return 1 + tilt, f"soft vs {opp} (+{int(tilt*100)}%)"
    if tilt <= -0.04:
        return 1 + tilt, f"tough vs {opp} ({int(tilt*100)}%)"
    return 1 + tilt, f"vs {opp}"


@dataclass
class LineupPlayer:
    name: str
    position: str
    team: str
    base_week: float
    adj_week: float
    matchup_note: str
    slot: Optional[str] = None    # assigned lineup slot, or None if benched


@dataclass
class StartSit:
    week: int
    starters: list[LineupPlayer]
    bench: list[LineupPlayer]
    close_calls: list[str] = field(default_factory=list)


# default FLEX-eligible
_FLEX_POS = {"RB", "WR", "TE"}


def start_sit(my_roster: list[tuple[str, str]], pool, cfg: E.LeagueConfig,
              week: int, scoring_key: str = "half") -> StartSit:
    """Optimal weekly lineup by matchup-adjusted projection."""
    scoring = cfg.scoring
    idx = {p.name: p for p in pool}
    graded: list[LineupPlayer] = []
    for name, pos in my_roster:
        p = idx.get(name)
        if not p:
            continue
        base = _weekly_points(p, scoring)
        mult, note = _matchup_multiplier(p.team, pos, week)
        graded.append(LineupPlayer(name, pos, p.team, round(base, 1),
                                   round(base * mult, 1), note))

    # lineup slots from league config
    starters_needed = dict(cfg.starters)
    flex_n = starters_needed.pop("FLEX", 0)

    graded.sort(key=lambda g: g.adj_week, reverse=True)
    starters: list[LineupPlayer] = []
    remaining = list(graded)

    # fill fixed slots first (best adj at each position)
    for pos, n in starters_needed.items():
        picks = [g for g in remaining if g.position == pos][:n]
        for g in picks:
            g.slot = pos
            starters.append(g)
            remaining.remove(g)
    # fill FLEX from best remaining flex-eligible
    flex_pool = [g for g in remaining if g.position in _FLEX_POS]
    for g in flex_pool[:flex_n]:
        g.slot = "FLEX"
        starters.append(g)
        remaining.remove(g)

    bench = remaining
    # close calls: a benched player within 1.5 pts of a same-eligibility starter
    close = []
    for b in bench:
        if b.position in _FLEX_POS:
            comp = [s for s in starters if s.slot in ("FLEX", b.position)]
        else:
            comp = [s for s in starters if s.slot == b.position]
        if comp:
            weakest = min(comp, key=lambda s: s.adj_week)
            if 0 <= weakest.adj_week - b.adj_week <= 1.5:
                close.append(f"{b.name} ({b.adj_week}) ~ {weakest.name} "
                             f"({weakest.adj_week}) at {weakest.slot}")
    return StartSit(week=week, starters=starters, bench=bench, close_calls=close)


# ---------------------------------------------------------------------------
# Trade evaluator
# ---------------------------------------------------------------------------
@dataclass
class TradeSide:
    players: list[tuple[str, str]]     # (name, position)
    total_vorp: float
    best_starter_vorp: float
    detail: list[tuple[str, float]]


@dataclass
class TradeEval:
    give: TradeSide
    get: TradeSide
    vorp_delta: float                  # get - give (season value swing)
    verdict: str
    note: str


def _vorp_map(pool, cfg: E.LeagueConfig) -> dict[str, "E.PlayerValue"]:
    pvs = [E.PlayerValue(p.name, p.name, p.position, p.team,
                         E.project_points(p.stats, cfg.scoring)) for p in pool]
    E.compute_vorp(pvs, cfg)
    return {pv.name: pv for pv in pvs}


def _side(players, vmap) -> TradeSide:
    detail = [(n, round(vmap[n].vorp, 1)) for n, _ in players if n in vmap]
    tot = round(sum(v for _, v in detail), 1)
    best = round(max((v for _, v in detail), default=0.0), 1)
    return TradeSide(players=players, total_vorp=tot, best_starter_vorp=best,
                     detail=detail)


def evaluate_trade(give: list[tuple[str, str]], get: list[tuple[str, str]],
                   pool, cfg: E.LeagueConfig,
                   scoring_key: str = "half") -> TradeEval:
    """Compare what you give vs get on VORP (season value over replacement).
    Also weighs best-starter value — 2-for-1s that downgrade your best starter
    are flagged even when total VORP says 'win'."""
    vmap = _vorp_map(pool, cfg)
    g_side = _side(give, vmap)
    r_side = _side(get, vmap)
    delta = round(r_side.total_vorp - g_side.total_vorp, 1)

    # consolidation check: getting the single best player matters (starter slots
    # are scarce). If you give more bodies than you get, the best-player swing
    # is the tiebreaker.
    best_swing = round(r_side.best_starter_vorp - g_side.best_starter_vorp, 1)

    if delta >= 15:
        verdict = "WIN"
    elif delta <= -15:
        verdict = "LOSE"
    else:
        verdict = "FAIR"

    notes = [f"season VORP swing {'+' if delta >= 0 else ''}{delta}"]
    if len(get) < len(give) and best_swing > 0:
        notes.append(f"consolidates into a better starter (+{best_swing} best-player VORP) — good if you have the bench depth")
    if len(get) > len(give) and best_swing < 0:
        notes.append(f"you're trading your best player down ({best_swing} best-player VORP) for depth — only worth it if you need bodies")
    note = "; ".join(notes)
    return TradeEval(give=g_side, get=r_side, vorp_delta=delta,
                     verdict=verdict, note=note)
