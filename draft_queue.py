"""
Draft Queue — a reconstructing pick-order planner.

The Board answers "best player available NOW." The Queue answers "the SEQUENCE
of picks that builds the best full roster," accounting for the snake clock: some
players survive to your next pick, some won't. Take the one that won't now;
queue the one that will for later.

Mechanic (recomputed from scratch every call, so it reconstructs after any pick):
  1. Rank available players by the edge composite (reuses edge_engine.recommend).
  2. Estimate each player's survival to each of YOUR future snake picks via the
     Prophecy greedy rollout (opponent-DNA aware).
  3. Walk your future picks in order. At each slot, from the players likely to
     BE there, pick the best one that fills an open roster need (or best value).
     Crucially: if an elite player at need-position A will NOT survive to your
     next pick but one at position B will, slot A now and B later.
  4. Return the ordered queue with a per-slot reason (need + survival + value).

INFORMATIONAL planning aid — it never mutates any player's projection/VORP.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import engine as E
import edge_engine as X
import prophecy as PR


@dataclass
class QueueSlot:
    my_overall: int              # the snake pick number this fills
    round_no: int
    name: str
    position: str
    team: str
    composite: float
    survival_here: Optional[float]   # P(available at THIS pick)
    reason: str


# starter targets then depth — the build we plan toward
_BUILD_ORDER = ["RB", "WR", "RB", "WR", "TE", "QB", "RB", "WR", "FLEX",
                "DST", "K"]
_TARGET_DEPTH = {"QB": 2, "RB": 5, "WR": 5, "TE": 2, "K": 1, "DST": 1}


def _open_needs(counts: dict[str, int]) -> dict[str, int]:
    return {pos: max(0, _TARGET_DEPTH.get(pos, 0) - counts.get(pos, 0))
            for pos in _TARGET_DEPTH}


def build_queue(pool, cfg: E.LeagueConfig, drafted: set[str],
                my_roster: list[tuple[str, str]], current_overall: int,
                opponents=None, scoring_key: str = "half",
                max_slots: int = 8) -> list[QueueSlot]:
    """Plan the next `max_slots` of YOUR picks. Reconstructs from the passed
    drafted set, so calling it after every pick gives a live-updating queue."""
    # my future snake picks from here on
    my_picks = [op for op in cfg.my_overall_picks() if op > current_overall]
    if not my_picks:
        return []
    my_picks = my_picks[:max_slots]

    # rank available by composite once (need is folded in by recommend)
    roster = X.Roster(players=list(my_roster))
    recs = X.recommend(pool, cfg, roster, set(drafted),
                       current_overall=current_overall, scoring_key=scoring_key,
                       top_n=120, opponents=opponents)
    rec_by_name = {r.name: r for r in recs}

    # survival to each of my picks via one prophecy rollout up to my last pick
    horizon = (my_picks[-1] - current_overall) + 1
    preds = PR.predict_board(pool, cfg, set(drafted), current_overall,
                             opponents=opponents, scoring_key=scoring_key,
                             horizon=max(horizon, 1))
    # a player is "likely gone by pick P" if the greedy rollout consumed him at
    # an overall < P. Build the consumed-at map.
    consumed_at: dict[str, int] = {}
    for pr in preds:
        if pr.top:
            consumed_at.setdefault(pr.top[0][0], pr.overall)

    def survives_to(name: str, my_ov: int) -> float:
        """P(player still available AT my pick `my_ov`). Only OPPONENT picks
        strictly before my_ov can take him. If nobody is projected to take him
        before my_ov, he's ~certain; the earlier he's projected gone, the less
        likely he lasts. The immediate on-the-clock pick is ~certain."""
        # picks strictly between now and my_ov that belong to OTHER teams
        opp_picks_before = max(0, (my_ov - current_overall))
        if opp_picks_before == 0:
            return 0.99   # I'm on the clock for this pick — it's mine to take
        c = consumed_at.get(name)
        if c is None or c >= my_ov:
            return 0.92   # greedy rollout never takes him before my pick
        # consumed before my pick: convert the gap into a survival prob.
        # right before my pick (gap 1) ~ coin flip; long before ~ gone.
        gap = my_ov - c
        return max(0.03, 0.55 - min(gap, 25) * 0.022)

    counts: dict[str, int] = {}
    for _, pos in my_roster:
        counts[pos] = counts.get(pos, 0) + 1

    used: set[str] = set()
    queue: list[QueueSlot] = []

    for my_ov in my_picks:
        needs = _open_needs(counts)
        rnd = ((my_ov - 1) // cfg.teams) + 1

        # candidate = available, not already queued, ranked by composite
        best = None
        best_key = None
        for r in recs:
            if r.name in used or r.name in drafted:
                continue
            surv = survives_to(r.name, my_ov)
            need_here = needs.get(r.position, 0) > 0
            # prefer: fills a need AND unlikely to survive to my NEXT pick
            # scoring: composite, boosted by need, boosted by scarcity (low surv)
            key = r.composite
            if need_here:
                key += 25
            # scarcity urgency: if he won't last, take him now
            if surv <= 0.3:
                key += 15
            elif surv >= 0.8:
                key -= 8      # can wait -> deprioritize taking him this early
            if best_key is None or key > best_key:
                best_key, best = key, (r, surv, need_here)

        if not best:
            break
        r, surv, need_here = best
        used.add(r.name)
        counts[r.position] = counts.get(r.position, 0) + 1

        reasons = []
        if need_here:
            reasons.append(f"fills {r.position} need")
        if surv <= 0.3:
            reasons.append(f"won't last ({int(surv*100)}% to here)")
        elif surv >= 0.8:
            reasons.append("safe pick / could wait")
        if r.value_vs_adp and r.value_vs_adp >= 8:
            reasons.append(f"value +{int(r.value_vs_adp)} vs ADP")
        reason = "; ".join(reasons) or "best available value"

        queue.append(QueueSlot(
            my_overall=my_ov, round_no=rnd, name=r.name, position=r.position,
            team=r.team, composite=r.composite, survival_here=round(surv, 2),
            reason=reason))

    return queue
