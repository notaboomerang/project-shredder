"""
Simulation core — Monte Carlo the rest of the draft.

Powers three galaxy-brain features from one engine:
  • survival DISTRIBUTION — run the remaining draft N times given opponent
    tendencies, count how often each player is still there at your next pick
    (a probability cloud, not a single logistic point-estimate).
  • championship EQUITY — project every team's starting-lineup points at the
    end of each simulated draft; your share of "best roster" outcomes ≈ your
    modeled title odds. Updates every pick.
  • GHOST DRAFT — a shadow AI drafts a parallel team from your slot using pure
    VORP; compare your real roster's projected points to the ghost's.

Dependency-light (stdlib random only). All pure functions.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

import engine as E
import projections as P
import opponents as OPP


def _snake_slot(overall: int, teams: int) -> int:
    rnd = (overall - 1) // teams + 1
    idx = (overall - 1) % teams
    return idx + 1 if rnd % 2 == 1 else teams - idx


def _pool_values(pool, cfg, drafted):
    """Available players scored + VORP'd for this format."""
    pvs = []
    meta = {}
    for raw in pool:
        if raw.name in drafted:
            continue
        pts = E.project_points(raw.stats, cfg.scoring)
        pvs.append(E.PlayerValue(raw.name, raw.name, raw.position, raw.team, pts))
        meta[raw.name] = raw
    E.compute_vorp(pvs, cfg)
    return pvs, meta


def _opp_pick(available, slot, opponents, needs) -> Optional[E.PlayerValue]:
    """Simulate one opponent pick: weight top available by their tendency +
    a little randomness, then draw. Streamable K/DST deprioritized early."""
    if not available:
        return None
    top = available[:10]
    weights = []
    prof = opponents.profiles.get(slot) if opponents else None
    for rank, pv in enumerate(top):
        # steep rank decay so the BEST available is usually taken (realistic);
        # tendencies + a little noise reshuffle the margins.
        w = (10.0 - rank) ** 2
        if prof:
            w *= prof.pos_multiplier(pv.position, None, False)
        if pv.position in ("K", "DST"):
            w *= 0.02
        weights.append(max(0.01, w))
    total = sum(weights) or 1.0
    r = random.random() * total
    acc = 0.0
    for pv, w in zip(top, weights):
        acc += w
        if r <= acc:
            return pv
    return top[0]


@dataclass
class SimResult:
    survival: dict          # name -> P(available at your next pick)
    championship_equity: float   # your modeled title share 0..1
    ghost_points: float
    your_proj_points: float
    n: int


def _my_best_pick(avail, my_positions, cfg, rnd):
    """My pick inside a sim: best-available by VORP, but roster-aware so the
    equity estimate reflects a LEGAL, sane build — not 3 TEs / 2 QBs stacked
    just because they carry VORP. Mirrors the Board's discipline rails:
      QB<=2, TE<=2, DST<=1, K<=1; and once the QB/TE STARTER slot is filled we
      stop taking another (streamable positions never beat a scarce RB/WR in
      the flex). K/DST only in the last two rounds.
    """
    if not avail:
        return None
    have = {}
    for p in my_positions:
        have[p] = have.get(p, 0) + 1
    st = cfg.starters
    hard = {"QB": 2, "TE": 2, "DST": 1, "K": 1}
    late = rnd >= cfg.rounds - 1
    for pv in avail:                       # avail is VORP-sorted desc
        pos = pv.position
        if pos in ("K", "DST") and not late:
            continue
        if have.get(pos, 0) >= hard.get(pos, 99):
            continue
        # don't take a 2nd QB/TE once the single starter slot is filled
        if pos in ("QB", "TE") and have.get(pos, 0) >= st.get(pos, 1):
            continue
        return pv
    return avail[0]                         # everything capped -> best remaining


def simulate(pool, cfg: E.LeagueConfig, drafted: set, my_roster: list,
             current_overall: int, opponents=None, n: int = 300) -> SimResult:
    """Monte-Carlo the remaining draft n times."""
    teams = cfg.teams
    my_slot = cfg.draft_slot
    total_picks = teams * cfg.rounds
    my_overalls = set(cfg.my_overall_picks())
    next_overall = min((o for o in cfg.my_overall_picks() if o > current_overall),
                       default=None)

    base_pvs, meta = _pool_values(pool, cfg, drafted)
    base_names = [pv.name for pv in base_pvs]

    survive_counts = {nm: 0 for nm in base_names}
    equity_wins = 0
    ghost_total = 0.0
    your_total = 0.0

    for _ in range(n):
        avail = list(base_pvs)  # already VORP-sorted desc
        team_pts = {t: 0.0 for t in range(1, teams + 1)}
        # seed my current roster points AND positions (for roster-aware picks)
        my_positions = [pos for _nm, pos in my_roster]
        for nm, _pos in my_roster:
            raw = meta.get(nm)
            if raw:
                your_total_seed = E.project_points(raw.stats, cfg.scoring)
                team_pts[my_slot] += your_total_seed
        ghost_pts = team_pts[my_slot]
        seen_next = set()

        for ov in range(current_overall, total_picks + 1):
            if not avail:
                break
            slot = _snake_slot(ov, teams)
            if ov == next_overall:
                seen_next = {pv.name for pv in avail}
            if slot == my_slot:
                rnd = (ov - 1) // teams + 1
                pick = _my_best_pick(avail, my_positions, cfg, rnd)
                if pick is not None:
                    my_positions.append(pick.position)
            else:
                pick = _opp_pick(avail, slot, opponents, None)
            if pick is None:
                break
            team_pts[slot] += pick.proj_points
            if slot == my_slot:
                ghost_pts += pick.proj_points
            avail.remove(pick)

        for nm in seen_next:
            survive_counts[nm] += 1
        # equity: my team's RANK among all teams by projected points. Count a
        # "win" as a top-3 (playoff-caliber) finish — a stable title proxy that
        # doesn't collapse to 0 the way sole-first does.
        ranked = sorted(team_pts, key=team_pts.get, reverse=True)
        my_rank = ranked.index(my_slot) + 1
        if my_rank <= max(1, teams // 4):
            equity_wins += 1
        ghost_total += ghost_pts
        your_total += team_pts[my_slot]

    survival = {nm: round(c / n, 3) for nm, c in survive_counts.items()}
    return SimResult(
        survival=survival,
        championship_equity=round(equity_wins / n, 3),
        ghost_points=round(ghost_total / n, 1),
        your_proj_points=round(your_total / n, 1),
        n=n,
    )
