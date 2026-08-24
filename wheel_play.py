"""
Wheel Play — grab-now-vs-wait on the snake turn.

At slot 11 you pick at 11 & 14 with only Joe (slot 12) and one other between.
The question each turn: take the player NOW, or does he survive back to your
next pick so you can grab someone else first and still get him?

This computes, per candidate, the probability the SPECIFIC rivals picking
between your current pick and your next pick take him (using their learned/set
tendency profiles + ADP proximity), then returns a verdict:
  • "TAKE NOW"  — high chance a rival (named) grabs him before you pick again
  • "CAN WAIT"  — low chance; grab a scarcer need now, get him on the wheel back

Plus best_pair(): the highest-combined-value pair of players to target across
your current + next pick, treating the wheel as one decision.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import engine as E
import projections as P


def _snake_slot(overall: int, teams: int) -> int:
    rnd = (overall - 1) // teams + 1
    idx = (overall - 1) % teams
    return idx + 1 if rnd % 2 == 1 else teams - idx


@dataclass
class WheelVerdict:
    name: str
    position: str
    take_prob: float                 # P(a rival takes him before my next pick)
    verdict: str                     # "TAKE NOW" | "CAN WAIT" | "TOSS-UP"
    threat: Optional[str]            # named rival most likely to grab him
    reason: str


def _rival_take_prob(pv_rank_score: float, position: str, adp: Optional[float],
                     intervening_overalls: list[int], slot_of, opponents,
                     name_map) -> tuple[float, Optional[str]]:
    """P(any intervening rival takes this player) + the biggest single threat.
    pv_rank_score: 0..1 how attractive he is (VORP percentile)."""
    survive = 1.0
    worst_slot, worst_take = None, 0.0
    for ov in intervening_overalls:
        slot = slot_of(ov)
        prof = opponents.profiles.get(slot) if opponents else None
        # base desire: attractiveness + ADP proximity to this pick
        if adp is not None:
            prox = 1.0 / (1.0 + math.exp(-(ov - adp) / 4.0))
        else:
            prox = 0.2
        base = 0.6 * pv_rank_score + 0.4 * prox
        mult = prof.pos_multiplier(position, None, False) if prof else 1.0
        take = max(0.0, min(0.9, base * min(mult, 2.2) * 0.7))
        if take > worst_take:
            worst_take, worst_slot = take, slot
        survive *= (1.0 - take)
    threat = None
    if worst_slot is not None and name_map:
        threat = name_map.get(worst_slot, f"slot {worst_slot}")
    return round(1.0 - survive, 2), threat


def wheel(pool, cfg: E.LeagueConfig, drafted: set, current_overall: int,
          opponents=None, scoring_key: str = "half",
          slot_names: Optional[dict] = None, top_n: int = 12) -> list[WheelVerdict]:
    """Per-candidate take-now-vs-wait for the players you'd consider now."""
    my_picks = cfg.my_overall_picks()
    next_overall = next((o for o in my_picks if o > current_overall), None)
    if next_overall is None:
        return []
    intervening = list(range(current_overall, next_overall))  # rivals' picks
    # score + VORP the available pool
    pvs, meta = [], {}
    for raw in pool:
        if raw.name in drafted:
            continue
        pts = E.project_points(raw.stats, cfg.scoring)
        pvs.append(E.PlayerValue(raw.name, raw.name, raw.position, raw.team, pts))
        meta[raw.name] = raw
    if not pvs:
        return []
    E.compute_vorp(pvs, cfg)
    pvs.sort(key=lambda x: x.vorp, reverse=True)
    top = pvs[:top_n]
    max_v = top[0].vorp if top and top[0].vorp > 0 else 1.0

    out = []
    for pv in top:
        raw = meta[pv.name]
        adp = P.adp_for(raw, scoring_key)
        rank_score = max(0.0, min(1.0, pv.vorp / max_v))
        tp, threat = _rival_take_prob(rank_score, pv.position, adp, intervening,
                                      lambda o: _snake_slot(o, cfg.teams),
                                      opponents, slot_names)
        if tp >= 0.6:
            verdict = "TAKE NOW"
            reason = (f"{threat or 'a rival'} likely grabs him before your pick "
                      f"{next_overall} ({int(tp*100)}%).")
        elif tp <= 0.3:
            verdict = "CAN WAIT"
            reason = (f"Only {int(tp*100)}% a rival takes him — he should wheel "
                      f"back to your pick {next_overall}. Grab a scarcer need now.")
        else:
            verdict = "TOSS-UP"
            reason = f"{int(tp*100)}% a rival takes him — coin flip."
        out.append(WheelVerdict(name=pv.name, position=pv.position,
                                take_prob=tp, verdict=verdict, threat=threat,
                                reason=reason))
    return out


def best_pair(pool, cfg: E.LeagueConfig, drafted: set, current_overall: int,
              opponents=None, scoring_key: str = "half") -> Optional[dict]:
    """Best 2-player target across your current + next pick, treating the wheel
    as one decision: take the higher-value guy who WON'T survive now, and the
    one who WILL survive at your next pick. Maximizes combined VORP."""
    verdicts = wheel(pool, cfg, drafted, current_overall, opponents,
                     scoring_key, top_n=20)
    if not verdicts:
        return None
    # value map
    pvs, meta = [], {}
    for raw in pool:
        if raw.name in drafted:
            continue
        pts = E.project_points(raw.stats, cfg.scoring)
        pvs.append(E.PlayerValue(raw.name, raw.name, raw.position, raw.team, pts))
    E.compute_vorp(pvs, cfg)
    vorp = {pv.name: pv.vorp for pv in pvs}
    now_cands = [v for v in verdicts if v.verdict == "TAKE NOW"]
    wait_cands = [v for v in verdicts if v.verdict in ("CAN WAIT", "TOSS-UP")]
    now_cands.sort(key=lambda v: vorp.get(v.name, 0), reverse=True)
    wait_cands.sort(key=lambda v: vorp.get(v.name, 0), reverse=True)
    now = now_cands[0] if now_cands else (verdicts[0] if verdicts else None)
    wait = next((w for w in wait_cands if not now or w.name != now.name), None)
    if not now:
        return None
    combined = vorp.get(now.name, 0) + (vorp.get(wait.name, 0) if wait else 0)
    return {
        "take_now": (now.name, now.position, round(vorp.get(now.name, 0), 1)),
        "then_wheel": ((wait.name, wait.position, round(vorp.get(wait.name, 0), 1))
                       if wait else None),
        "combined_vorp": round(combined, 1),
        "logic": (f"Grab {now.name} now ({now.threat or 'a rival'} would snag him), "
                  + (f"then {wait.name} should wheel back to your next pick."
                     if wait else "no clear wheel-back target yet.")),
    }



# --------------------------------------------------------------------------- TD combos
def td_combos(pool, scoring_key: str = "half", min_tds: float = 40.0):
    """Find same-team QB + RB pairs whose COMBINED projected touchdowns
    (QB pass_td + rush_td, RB rush_td + rec_td) clear `min_tds`. These are the
    scoring-machine offenses where rostering both concentrates TD upside — a
    correlation stack. Returns a list of dicts sorted by combined TDs desc:
      {team, qb, rb, qb_td, rb_td, combined, qb_adp, rb_adp}
    """
    import projections as P
    by_team_qb = {}
    by_team_rb = {}
    for raw in pool:
        s = raw.stats or {}
        if raw.position == "QB":
            td = float(s.get("pass_td", 0)) + float(s.get("rush_td", 0))
            by_team_qb.setdefault(raw.team, []).append((raw, td))
        elif raw.position == "RB":
            td = float(s.get("rush_td", 0)) + float(s.get("rec_td", 0))
            by_team_rb.setdefault(raw.team, []).append((raw, td))

    combos = []
    for team, qbs in by_team_qb.items():
        rbs = by_team_rb.get(team, [])
        if not rbs:
            continue
        qb, qtd = max(qbs, key=lambda x: x[1])      # the team's top QB
        rb, rtd = max(rbs, key=lambda x: x[1])      # the team's top RB
        combined = qtd + rtd
        if combined >= min_tds:
            combos.append({
                "team": team, "qb": qb.name, "rb": rb.name,
                "qb_td": round(qtd, 1), "rb_td": round(rtd, 1),
                "combined": round(combined, 1),
                "qb_adp": P.adp_for(qb, scoring_key),
                "rb_adp": P.adp_for(rb, scoring_key),
            })
    combos.sort(key=lambda c: c["combined"], reverse=True)
    return combos


def combo_players(pool, scoring_key: str = "half", min_tds: float = 40.0) -> set:
    """Set of player names that are part of a 40+ combined-TD QB/RB package —
    used to stamp a ➕ marker on the board/wheel."""
    names = set()
    for c in td_combos(pool, scoring_key, min_tds):
        names.add(c["qb"]); names.add(c["rb"])
    return names
