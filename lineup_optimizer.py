"""
Weekly lineup optimizer — start/sit with a narrative.

Given your roster and a week, this picks the optimal starting lineup (fills the
required slots + FLEX/SUPERFLEX by best projected weekly points) and, crucially,
explains WHY each player is started or benched in plain English — pulling the
reasoning from the same edge signals used at draft: matchup softness (pass-D /
this week's opponent), dome/venue, Vegas team total, target/RZ role, injury
risk, and raw projection.

Weekly projection = season projection / 17 (per-game baseline), then adjusted
by this week's opponent softness + venue + Vegas environment. Pure functions;
no global state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import engine as E
import matchups as M
import venue as VEN
import advanced_metrics as ADV
import projections as P


@dataclass
class StartSit:
    name: str
    position: str
    team: str
    slot: str                     # assigned slot ("RB","FLEX","BENCH", ...)
    weekly_points: float
    started: bool
    narrative: str


def _weekly_base(raw: P.RawPlayer, scoring: E.Scoring) -> float:
    return E.project_points(raw.stats, scoring) / 17.0


def _week_opponent(team: str, week: int) -> Optional[str]:
    sched = M.load_schedule().get(team)
    if not sched:
        return None
    return sched.get(week)


def _matchup_adj(team: str, position: str, week: int) -> tuple[float, str]:
    """Per-week adjustment + reason from THIS week's opponent.

    Prefers the REAL defense-vs-position read (defense_vs_position, from live
    play-by-play — fantasy pts/g that defense allows to this position vs league).
    Falls back to the seeded pass-TD-allowed table when that module/data is
    unavailable, so the optimizer always produces a number.
    """
    opp = _week_opponent(team, week)
    if opp is None:
        return 0.0, "on bye" if opp is None and week in _byes(team) else "matchup n/a"

    # 1) real defense-vs-position (pbp) — the same layer the screener/eruption use
    try:
        import defense_vs_position as DVP
        if position in DVP._POSITIONS:
            n = DVP.matchup_nudge(opp, position)
            if n.source in ("pbp", "cache"):
                adj = n.lean            # already in fantasy-pts/g terms
                if n.softness == "SOFT":
                    return adj, (f"juicy matchup vs {opp} — soft {position} D "
                                 f"(+{n.surplus_pg:g} pts/g vs league)")
                if n.softness == "TOUGH":
                    return adj, (f"tough matchup vs {opp} — stingy {position} D "
                                 f"({n.surplus_pg:g} pts/g vs league)")
                return adj, f"neutral matchup vs {opp} ({n.surplus_pg:+g} pts/g)"
    except Exception:
        pass

    # 2) fallback: seeded pass-TD-allowed table
    pd = M.load_pass_defense().get(opp, 26.5)
    soft = pd - 26.5
    if position in ("QB", "WR", "TE"):
        adj = soft * 0.08
        if soft >= 3:
            return adj, f"juicy matchup vs {opp} (soft pass-D, {pd} TD allowed)"
        if soft <= -3:
            return adj, f"tough matchup vs {opp} (stingy pass-D)"
        return adj, f"neutral matchup vs {opp}"
    return 0.0, f"vs {opp}"


def _byes(team: str) -> set[int]:
    sched = M.load_schedule().get(team) or {}
    return {w for w, o in sched.items() if o is None}


def optimize_week(roster_players: list[tuple[str, str]], pool: list[P.RawPlayer],
                  cfg: E.LeagueConfig, week: int,
                  scoring_key: str = "half") -> list[StartSit]:
    name_to_raw = {p.name: p for p in pool}
    scored: list[StartSit] = []

    for name, pos in roster_players:
        raw = name_to_raw.get(name)
        if not raw:
            continue
        base = _weekly_base(raw, cfg.scoring)
        reasons = []
        pts = base

        on_bye = week in _byes(raw.team)
        if on_bye:
            pts = 0.0
            reasons.append("ON BYE this week — cannot start")
        else:
            madj, mreason = _matchup_adj(raw.team, pos, week)
            pts += madj
            reasons.append(mreason)
            # venue
            vrep = VEN.venue_report(raw.team)
            if vrep and vrep.indoor_share >= 0.55 and pos in ("QB", "WR", "TE"):
                pts += 1.0
                reasons.append("dome-friendly environment")
            # advanced role / risk
            adelta, abadges = ADV.metric_adjustments(name, pos)
            pts += adelta * 0.1
            if abadges:
                reasons.append(abadges[0].lower())
            # ceiling (eruption) — a small upside nudge + context, half weight
            try:
                import eruption_watch as EW
                _sp = EW.eruption_watch(
                    [{"name": name, "team": raw.team, "position": pos}],
                    week=week)
                spots = _sp.get("spots") if isinstance(_sp, dict) else None
                if spots:
                    boost = spots[0].ceiling_boost
                    pts += 0.15 * boost
                    reasons.append(f"ceiling +{boost:g}")
            except Exception:
                pass
            # division-game factual context (no scoring effect)
            try:
                from divisions import division_tag
                opp_now = _week_opponent(raw.team, week)
                dt = division_tag(raw.team, opp_now) if opp_now else ""
                if dt:
                    reasons.append(dt)
            except Exception:
                pass

        scored.append(StartSit(
            name=name, position=pos, team=raw.team, slot="BENCH",
            weekly_points=round(pts, 1), started=False,
            narrative="; ".join(reasons),
        ))

    _assign_starters(scored, cfg)
    for s in scored:
        verb = "START" if s.started else "SIT"
        s.narrative = f"{verb} ({s.slot}, {s.weekly_points} pts): {s.narrative}"
    scored.sort(key=lambda s: (not s.started, -s.weekly_points))
    return scored


def _assign_starters(scored: list[StartSit], cfg: E.LeagueConfig) -> None:
    """Greedy optimal fill: dedicated slots first (best proj at each position),
    then FLEX/SUPERFLEX from the best remaining eligible."""
    remaining = sorted(scored, key=lambda s: s.weekly_points, reverse=True)
    used = set()
    # dedicated slots
    for pos in ("QB", "RB", "WR", "TE", "DST", "K"):
        need = cfg.starters.get(pos, 0)
        picked = 0
        for s in remaining:
            if picked >= need:
                break
            if s.position == pos and id(s) not in used:
                s.started = True; s.slot = pos; used.add(id(s)); picked += 1
    # flex
    for slot, elig in (("FLEX", ("RB", "WR", "TE")),
                       ("SUPERFLEX", ("QB", "RB", "WR", "TE"))):
        need = cfg.starters.get(slot, 0)
        picked = 0
        for s in remaining:
            if picked >= need:
                break
            if s.position in elig and id(s) not in used:
                s.started = True; s.slot = slot; used.add(id(s)); picked += 1
