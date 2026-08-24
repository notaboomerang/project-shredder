"""
Mock Draft — practice against realistic AI bots, right inside the app.

Rather than hooking ESPN's ephemeral mock lobby (undocumented, throwaway), this
runs a full simulated snake draft in the app: the bots at every other slot
auto-pick using the same tendency model + VORP + ADP the rest of the engine
uses, pausing whenever it's YOUR turn. You draft against them exactly as you
would live — same board, same Prophecy, same Wheel Play, same injury chips —
so you can feel whether the picks are realistic before Monday.

State is a plain dict the app stores in session; the app drives it: call
bots_pick_until_me() after each of your picks to advance the bots to your next
turn. No DNA needed (bots use default/tunable tendencies).
"""
from __future__ import annotations

import random
from typing import Optional

import engine as E
import projections as P


def _snake_slot(overall: int, teams: int) -> int:
    rnd = (overall - 1) // teams + 1
    idx = (overall - 1) % teams
    return idx + 1 if rnd % 2 == 1 else teams - idx


def _bot_pick(available, slot, opponents, roster_counts) -> Optional[object]:
    """Realistic bot selection. Early picks hew close to best-available/ADP
    (a real room rarely lets a top-5 board value slide), with the slot's
    tendency profile + roster-need as the tiebreaker. Randomness grows only in
    the later rounds where real drafts genuinely diverge. K/DST deferred until
    the roster is nearly full; QB/TE pulled up once the need is real."""
    if not available:
        return None
    picks_made = sum(roster_counts.values())
    prof = opponents.profiles.get(slot) if opponents else None

    # how many starters of each the bot still wants
    need = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DST": 1}
    have = dict(roster_counts)
    def still_needs(pos):
        return have.get(pos, 0) < need.get(pos, 0)

    # window widens as the draft goes (early = tight, chalk; late = loose)
    window = 4 if picks_made < 4 else (8 if picks_made < 8 else 14)
    top = available[:window]

    weights = []
    for rank, pv in enumerate(top):
        # steep decay early (near best-available), flatter late
        decay = (window - rank) ** (2.4 if picks_made < 5 else 1.4)
        w = float(decay)
        if prof:
            w *= prof.pos_multiplier(pv.position, None, False)
        pos = pv.position
        # roster-need shaping
        if still_needs(pos):
            w *= 1.35
        if have.get(pos, 0) >= need.get(pos, 0) and pos in ("RB", "WR"):
            # already have starters; flex depth ok but eased
            w *= 0.7 if have.get(pos, 0) < 4 else 0.3
        # QB: don't take a 2nd; pull the 1st up in mid rounds if still QB-less
        if pos == "QB":
            if have.get("QB", 0) >= 1:
                w *= 0.02
            elif picks_made >= 6:
                w *= 1.6           # rounds 7+ with no QB -> grab one
            else:
                w *= 0.5           # early QB rare unless tendency says so
        # TE: pull up once past mid-draft if still empty
        if pos == "TE":
            if have.get("TE", 0) >= 1:
                w *= 0.1
            elif picks_made >= 5:
                w *= 1.3
        # K/DST: only near the end, and only one each
        if pos in ("K", "DST"):
            if have.get(pos, 0) >= 1 or picks_made < 12:
                w *= 0.001
            else:
                w *= 2.0
        weights.append(max(0.001, w))

    tot = sum(weights)
    r = random.random() * tot
    acc = 0.0
    for pv, w in zip(top, weights):
        acc += w
        if r <= acc:
            return pv
    return top[0]


def bots_pick_until_me(pool, cfg: E.LeagueConfig, drafted: set,
                       team_rosters: dict, current_overall: int,
                       opponents=None, pick_log=None) -> dict:
    """Advance the draft: bots auto-pick every non-user slot until it's the
    user's turn (or the draft ends). Mutates drafted / team_rosters / pick_log
    in place and returns a summary of what the bots just did.

    Returns {picks: [(overall, name, pos, slot)], now_overall, your_turn}."""
    teams = cfg.teams
    total = teams * cfg.rounds
    my_slot = cfg.draft_slot
    made = []
    ov = current_overall

    while ov <= total:
        slot = _snake_slot(ov, teams)
        if slot == my_slot:
            break  # hand control back to the user
        # rescore available pool (cheap enough for a mock)
        pvs = []
        for raw in pool:
            if raw.name in drafted:
                continue
            pts = E.project_points(raw.stats, cfg.scoring)
            pvs.append(E.PlayerValue(raw.name, raw.name, raw.position,
                                     raw.team, pts))
        if not pvs:
            break
        E.compute_vorp(pvs, cfg)
        pvs.sort(key=lambda x: x.vorp, reverse=True)
        rc = {}
        for nm in team_rosters.get(slot, []):
            # position lookup via pool
            pass
        # build roster counts by position for this slot
        name_pos = {raw.name: raw.position for raw in pool}
        rc = {}
        for nm in team_rosters.get(slot, []):
            p = name_pos.get(nm)
            if p:
                rc[p] = rc.get(p, 0) + 1
        pick = _bot_pick(pvs, slot, opponents, rc)
        if pick is None:
            break
        drafted.add(pick.name)
        team_rosters.setdefault(slot, []).append(pick.name)
        made.append((ov, pick.name, pick.position, slot))
        if pick_log is not None:
            pick_log.append((ov, pick.name, pick.position, slot))
        ov += 1

    return {"picks": made, "now_overall": ov,
            "your_turn": (ov <= total and _snake_slot(ov, teams) == my_slot)}
