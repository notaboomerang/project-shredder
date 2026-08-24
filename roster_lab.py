"""
Roster Lab — the structural edges a per-player projection misses.

Four analyzers, all INFORMATIONAL (never mutate projection/VORP/composite —
honors KC's no-artificial-value rule). Each reads the real pool + the live
2026 schedule (data/schedule.json).

1. bye_collisions(roster)      — too many starters sharing a bye week
2. stack_clusters(roster)      — multiple picks on the same NFL team (corr. risk
                                 for non-QB/receiver pairs; upside for QB+catcher)
3. tier_cliff(pool, drafted)   — positional run / tier cliff alarm vs YOUR needs
4. handcuffs(pool)             — who backs up whom (contingent value)

Everything degrades gracefully if data is missing.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import projections as P

_DATA = os.path.join(os.path.dirname(__file__), "data")

# ESPN uses WSH; the pool uses WAS. Normalize both ways.
_TEAM_ALIAS = {"WSH": "WAS", "WAS": "WAS", "JAC": "JAX", "LA": "LAR"}


def _norm_team(t: str) -> str:
    t = (t or "").upper()
    return _TEAM_ALIAS.get(t, t)


# Correction map for KNOWN-stale pool team tags (editable at
# data/player_team_overrides.json). Fixes cases where the seed pool carries a
# wrong current team. Seed NOTHING from memory — only add entries verified
# against a live source, or the "fix" becomes the bug.
_PLAYER_TEAM_FIX: dict[str, str] = {}


def _player_team(name: str, pool_team: str) -> str:
    """Real current team for a player: override map wins over the pool's tag."""
    ov = {}
    _p = os.path.join(_DATA, "player_team_overrides.json")
    if os.path.exists(_p):
        try:
            with open(_p, encoding="utf-8") as f:
                ov = json.load(f)
        except Exception:
            ov = {}
    fixed = ov.get(name) or _PLAYER_TEAM_FIX.get(name)
    return _norm_team(fixed) if fixed else _norm_team(pool_team)


def load_schedule() -> dict[str, dict[int, Optional[str]]]:
    path = os.path.join(_DATA, "schedule.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            return {_norm_team(tm): {int(w): (_norm_team(o) if o else None)
                                     for w, o in wks.items()}
                    for tm, wks in raw.items()}
        except Exception:
            return {}
    return {}


# ---------------------------------------------------------------------------
# 1. BYE-WEEK COLLISIONS
# ---------------------------------------------------------------------------
@dataclass
class ByeCollision:
    week: int
    players: list[tuple[str, str]]   # (name, position)
    severity: str                    # "danger" | "warn" | "ok"
    note: str


def _pool_index(pool) -> dict[str, "P.RawPlayer"]:
    return {p.name: p for p in pool}


def bye_collisions(roster: list[tuple[str, str]], pool) -> list[ByeCollision]:
    """roster = [(name, position)]. Flags weeks where too many of YOUR
    starters are on bye at once (a real, common way a good roster loses)."""
    idx = _pool_index(pool)
    by_week: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for name, pos in roster:
        p = idx.get(name)
        if p and p.bye:
            by_week[int(p.bye)].append((name, pos))
    out: list[ByeCollision] = []
    for wk in sorted(by_week):
        grp = by_week[wk]
        n = len(grp)
        # same-position clustering is worse (can't field a starter)
        pos_counts = defaultdict(int)
        for _, pos in grp:
            pos_counts[pos] += 1
        worst_pos, worst_ct = (max(pos_counts.items(), key=lambda kv: kv[1])
                               if pos_counts else ("", 0))
        if n >= 4 or worst_ct >= 3:
            sev, note = "danger", f"{n} starters on bye wk{wk} ({worst_ct}× {worst_pos}) — you may not field a full lineup"
        elif n == 3 or worst_ct == 2:
            sev, note = "warn", f"{n} starters on bye wk{wk} — thin that week"
        else:
            sev, note = "ok", f"{n} on bye wk{wk}"
        out.append(ByeCollision(wk, grp, sev, note))
    return out


# ---------------------------------------------------------------------------
# 2. SAME-TEAM STACK CLUSTERS
# ---------------------------------------------------------------------------
@dataclass
class TeamCluster:
    team: str
    players: list[tuple[str, str]]
    kind: str          # "stack" (QB+catcher, good) | "concentration" (risk)
    note: str


def stack_clusters(roster: list[tuple[str, str]], pool) -> list[TeamCluster]:
    idx = _pool_index(pool)
    by_team: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for name, pos in roster:
        p = idx.get(name)
        if p and p.team:
            by_team[_norm_team(p.team)].append((name, pos))
    out: list[TeamCluster] = []
    for team, grp in by_team.items():
        if len(grp) < 2:
            continue
        positions = {pos for _, pos in grp}
        has_qb = "QB" in positions
        has_catcher = bool(positions & {"WR", "TE"})
        if has_qb and has_catcher:
            out.append(TeamCluster(
                team, grp, "stack",
                f"QB + pass-catcher stack on {team} — a TD scores on BOTH spots (upside compounds)"))
        elif len(grp) >= 2 and positions <= {"RB", "WR", "TE"}:
            out.append(TeamCluster(
                team, grp, "concentration",
                f"{len(grp)} non-QB skill players on {team} — shared bye + one bad offense sinks both (correlated risk)"))
    return out


# ---------------------------------------------------------------------------
# 3. TIER-CLIFF / ROSTER-NEED POSITIONAL-RUN ALARM
# ---------------------------------------------------------------------------
_STARTER_TARGETS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DST": 1}
# FLEX means you really want ~3 RB + ~3 WR by mid-draft
_DESIRED_DEPTH = {"RB": 4, "WR": 4, "TE": 1, "QB": 1, "K": 1, "DST": 1}


@dataclass
class CliffAlarm:
    position: str
    remaining_tier: int          # how many "startable-tier" players remain
    my_count: int
    need: int
    urgency: str                 # "now" | "soon" | "ok"
    note: str


def tier_cliff(pool, drafted: set[str], my_roster: list[tuple[str, str]],
               scoring_key: str = "half", tier_gap: float = 18.0) -> list[CliffAlarm]:
    """Alarm when a position I still NEED is about to fall off a tier cliff.
    tier_gap = projected-points drop that defines the edge of the current tier."""
    import engine as E
    cfg_scoring = E.Scoring.preset(scoring_key)

    # my positional counts
    mine = defaultdict(int)
    for _, pos in my_roster:
        mine[pos] += 1

    # available by position, sorted by projection desc
    avail_by_pos: dict[str, list[float]] = defaultdict(list)
    for p in pool:
        if p.name in drafted:
            continue
        try:
            pts = E.project_points(p.stats, cfg_scoring)
        except Exception:
            continue
        avail_by_pos[p.position].append(pts)
    for pos in avail_by_pos:
        avail_by_pos[pos].sort(reverse=True)

    out: list[CliffAlarm] = []
    for pos, want in _DESIRED_DEPTH.items():
        have = mine.get(pos, 0)
        need = max(0, want - have)
        if need <= 0:
            continue
        pts = avail_by_pos.get(pos, [])
        if not pts:
            continue
        # count players in the current top tier (before the first big gap)
        tier_n = 1
        for i in range(1, min(len(pts), 40)):
            if pts[i - 1] - pts[i] >= tier_gap:
                break
            tier_n += 1
        # urgency: if the tier is about to be drained relative to teams behind you
        if tier_n <= need:
            urg, note = "now", f"only {tier_n} startable {pos} left and you need {need} — the {pos} cliff is HERE, pivot now"
        elif tier_n <= need + 2:
            urg, note = "soon", f"{tier_n} startable {pos} left (you need {need}) — cliff within a round or two"
        else:
            urg, note = "ok", f"{tier_n} startable {pos} remain (need {need})"
        out.append(CliffAlarm(pos, tier_n, have, need, urg, note))
    # surface urgent first
    order = {"now": 0, "soon": 1, "ok": 2}
    out.sort(key=lambda a: order[a.urgency])
    return out


# ---------------------------------------------------------------------------
# 4. HANDCUFFS (contingent value)
# ---------------------------------------------------------------------------
@dataclass
class Handcuff:
    starter: str
    starter_team: str
    backup: str                  # best available same-team RB behind the starter
    backup_available: bool
    note: str


def handcuffs(pool, drafted: set[str], my_roster: list[tuple[str, str]],
              scoring_key: str = "half") -> list[Handcuff]:
    """For each RB on MY roster, find the top same-team RB behind him = his
    handcuff (contingent bellcow if the starter goes down)."""
    import engine as E
    cfg_scoring = E.Scoring.preset(scoring_key)
    idx = _pool_index(pool)

    # rank RBs per team by projection (team-corrected)
    team_rbs: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for p in pool:
        if p.position != "RB" or not p.team:
            continue
        try:
            pts = E.project_points(p.stats, cfg_scoring)
        except Exception:
            pts = 0.0
        team_rbs[_player_team(p.name, p.team)].append((p.name, pts))
    for t in team_rbs:
        team_rbs[t].sort(key=lambda kv: kv[1], reverse=True)

    out: list[Handcuff] = []
    for name, pos in my_roster:
        if pos != "RB":
            continue
        p = idx.get(name)
        if not p or not p.team:
            continue
        team = _player_team(name, p.team)
        backups = [nm for nm, _ in team_rbs.get(team, []) if nm != name]
        if not backups:
            continue
        backup = backups[0]
        avail = backup not in drafted
        note = (f"{backup} is {name}'s handcuff ({team}) — "
                + ("grab him late to lock the backfield" if avail
                   else "already drafted"))
        out.append(Handcuff(name, team, backup, avail, note))
    return out


# ---------------------------------------------------------------------------
# 5. PLAYOFF SLATE (weeks 15-17) — leagues are won here
# ---------------------------------------------------------------------------
@dataclass
class PlayoffSlate:
    player: str
    team: str
    weeks: list[tuple[int, Optional[str]]]   # [(15, opp), (16, opp), (17, opp)]
    grade: str
    note: str


def playoff_slate(name: str, pool, playoff_weeks=(15, 16, 17)) -> Optional[PlayoffSlate]:
    """Grade a player's fantasy-playoff opponents (softness of pass D) — the
    single most underrated draft input. Reuses matchups pass-D softness."""
    import matchups as M
    idx = _pool_index(pool)
    p = idx.get(name)
    if not p or not p.team:
        return None
    team = _norm_team(p.team)
    sched = load_schedule().get(team)
    if not sched:
        return None
    pd = M.load_pass_defense()
    avg = M._LEAGUE_AVG_PASS_TD
    wk_opps, softs = [], []
    for wk in playoff_weeks:
        opp = sched.get(wk)
        wk_opps.append((wk, opp))
        if opp:
            softs.append(pd.get(opp, avg) - avg)
    if not softs:
        return None
    po_soft = sum(softs) / len(softs)
    if po_soft >= 3:
        grade, note = "A", f"soft playoff slate (wks 15-17 avg +{po_soft:.1f} pass-TD vs avg) — smash spot when it counts"
    elif po_soft >= 0:
        grade, note = "B", f"neutral-to-favorable playoff slate (+{po_soft:.1f})"
    elif po_soft >= -3:
        grade, note = "C", f"slightly tough playoff slate ({po_soft:.1f})"
    else:
        grade, note = "D", f"brutal playoff slate ({po_soft:.1f} vs avg) — faces top pass Ds wks 15-17"
    return PlayoffSlate(name, team, wk_opps, grade, note)
