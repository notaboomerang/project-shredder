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


def _adp_key(cfg) -> str:
    """Map the league's scoring to the ADP field to use as the bots' board."""
    rec = getattr(getattr(cfg, "scoring", None), "reception", 0.0) or 0.0
    if rec >= 0.9:
        return "ppr"
    if rec >= 0.4:
        return "half"
    return "std"


def _get_adp(raw, key: str):
    """Pull a player's ADP for the given scoring key. Returns None if absent."""
    adp = getattr(raw, "adp", None)
    if isinstance(adp, dict):
        v = adp.get(key)
        if v is None:  # fall back across scoring formats
            v = adp.get("half") or adp.get("ppr") or adp.get("std")
        return float(v) if v is not None else None
    if isinstance(adp, (int, float)):
        return float(adp)
    return None


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

    # `available` is ALREADY sorted by ESPN-default ADP (market board). A real
    # room drafts near the top of that board, so consider a tight window and
    # decay steeply — rank 0 (best available by ADP) is the default pick, with
    # DNA/tendency and roster-need nudging the choice, plus mild reach/slide
    # randomness that grows in later rounds. NOTE: no QB/K/DST VORP-suppression
    # here — ADP already places them correctly (QBs sit in the 30s), so the
    # market board does that work and our engine never touches the bots.
    window = 5 if picks_made < 4 else (9 if picks_made < 8 else 15)
    top = available[:window]

    weights = []
    for rank, pv in enumerate(top):
        # steep decay early (hug ADP), flatter late (rooms diverge)
        decay = (window - rank) ** (2.6 if picks_made < 5 else 1.5)
        w = float(decay)
        pos = pv.position
        # learned DNA / tendency lean (RB-heavy, WR-zealot, homer, etc.)
        if prof:
            w *= prof.pos_multiplier(pos, None, False)
        # roster-need: still want a starter here → nudge up; already set → ease
        if still_needs(pos):
            w *= 1.3
        elif pos in ("RB", "WR"):
            w *= 0.75 if have.get(pos, 0) < 4 else 0.35   # bench depth, eased
        # sanity guards that mirror real behavior (NOT value manipulation):
        # nobody drafts a 2nd QB/TE early, or a K/DST before the last rounds.
        if pos == "QB" and have.get("QB", 0) >= 1:
            w *= 0.03
        if pos == "TE" and have.get("TE", 0) >= 1:
            w *= 0.15
        if pos in ("K", "DST"):
            if have.get(pos, 0) >= 1 or picks_made < 12:
                w *= 0.001
            else:
                w *= 2.5          # fill the last slots
        weights.append(max(0.0001, w))

    tot = sum(weights)
    r = random.random() * tot
    acc = 0.0
    for pv, w in zip(top, weights):
        acc += w
        if r <= acc:
            return pv
    return top[0]


def pick_for_roster(available, cfg, roster_counts):
    """Best-ADP pick that respects roster construction, so an auto-drafted team
    is LEGAL (no 4-QB / 4-DST rosters). `available` must be ADP-sorted ascending.

    Priority: fill starters (QB/RB/WR/TE) → FLEX (RB/WR/TE) → DST → K → bench
    depth at RB/WR/TE. Hard caps: 1 K, 1 DST, 2 QB, 3 TE; RB/WR uncapped (flex +
    bench). Within an allowed set we take the best ADP, so we never reach for a
    2nd QB or a K early just because ADP surfaced one."""
    if not available:
        return None
    st = cfg.starters
    have = dict(roster_counts)
    total_slots = sum(st.values()) + cfg.bench

    def cnt(*pos):
        return sum(have.get(p, 0) for p in pos)

    # hard positional caps for a sane roster
    caps = {"QB": 2, "TE": 3, "DST": 1, "K": 1}

    # which positions are ALLOWED to be drafted right now, in priority order
    need_starter = []
    if have.get("QB", 0) < st.get("QB", 1):
        need_starter.append("QB")
    if have.get("RB", 0) < st.get("RB", 2):
        need_starter.append("RB")
    if have.get("WR", 0) < st.get("WR", 2):
        need_starter.append("WR")
    if have.get("TE", 0) < st.get("TE", 1):
        need_starter.append("TE")

    picks_made = sum(have.values())
    rounds_left = total_slots - picks_made

    # FLEX eligible once RB/WR/TE starters are in
    flex_ok = (have.get("RB", 0) >= st.get("RB", 2)
               and have.get("WR", 0) >= st.get("WR", 2))
    need_flex = flex_ok and (cnt("RB", "WR", "TE")
                             < st.get("RB", 2) + st.get("WR", 2)
                             + st.get("TE", 1) + st.get("FLEX", 1))

    # DST / K only when there are few enough rounds left that it's realistic,
    # and only one each — never before core is built.
    need_dst = have.get("DST", 0) < st.get("DST", 1) and rounds_left <= 4
    need_k = have.get("K", 0) < st.get("K", 1) and rounds_left <= 2

    if need_starter:
        allowed = set(need_starter)
    elif need_dst:
        allowed = {"DST"}
    elif need_k:
        allowed = {"K"}
    elif need_flex:
        allowed = {"RB", "WR", "TE"}
    else:
        allowed = {"RB", "WR", "TE"}   # bench depth at skill positions

    # take best-ADP player in the allowed set that isn't over its cap
    for pv in available:                      # already ADP-sorted
        pos = pv.position
        if pos not in allowed:
            continue
        if have.get(pos, 0) >= caps.get(pos, 99):
            continue
        return pv
    # fallback: best-ADP legal player under caps (avoids returning nothing)
    for pv in available:
        if have.get(pv.position, 0) < caps.get(pv.position, 99):
            return pv
    return available[0]


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
        # ── Bots draft off the MARKET (ESPN-default ADP) + their learned DNA,
        # NOT Shredder's VORP/edge engine. This keeps our ranking system walled
        # off from theirs: the opponents behave like a real ESPN room (whose
        # board is ADP-shaped — QBs go late, etc.), so the mock is a genuine
        # test of your edge vs. theirs rather than our engine drafting against
        # itself. DNA/tendency shaping happens inside _bot_pick.
        scoring_key = _adp_key(cfg)
        adp_by_name = {}
        pvs = []
        for raw in pool:
            if raw.name in drafted:
                continue
            pts = E.project_points(raw.stats, cfg.scoring)
            pv = E.PlayerValue(raw.name, raw.name, raw.position, raw.team, pts)
            adp = _get_adp(raw, scoring_key)
            pv.adp = adp
            adp_by_name[raw.name] = adp
            pvs.append(pv)
        if not pvs:
            break
        # Market board = sort by ADP ascending (undrafted ADP sinks to bottom).
        _NO_ADP = 9999.0
        pvs.sort(key=lambda x: (x.adp if x.adp else _NO_ADP))
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


def project_my_team(pool, cfg: E.LeagueConfig, opponents=None, seed=None):
    """Run a FULL snake draft and return YOUR projected roster. Bots draft off
    ESPN-default ADP + DNA (via bots_pick_until_me); your seat drafts a LEGAL
    roster via pick_for_roster (starters → FLEX → DST → K → depth, capped). This
    is the reusable engine behind the app's 'what will my team look like'
    projection — no artificial round cap, stall-guarded so it always terminates.

    Returns {my_roster: [(overall, name, pos)], pick_log: [(overall,name,pos,slot)],
             counts: {pos: n}}."""
    if seed is not None:
        random.seed(seed)
    teams = cfg.teams
    total = teams * cfg.rounds
    my_slot = cfg.draft_slot
    drafted, team_rosters, pick_log = set(), {}, []
    ov = 1
    pool_size = len(pool)

    while ov <= total and len(drafted) < pool_size:
        prev = ov
        r = bots_pick_until_me(pool, cfg, drafted, team_rosters, ov,
                               opponents=opponents, pick_log=pick_log)
        ov = r["now_overall"]
        if r.get("your_turn") and ov <= total and len(drafted) < pool_size:
            pvs = []
            for raw in pool:
                if raw.name in drafted:
                    continue
                pv = E.PlayerValue(raw.name, raw.name, raw.position, raw.team,
                                   E.project_points(raw.stats, cfg.scoring))
                pv.adp = _get_adp(raw, _adp_key(cfg))
                pvs.append(pv)
            if not pvs:
                break
            pvs.sort(key=lambda x: (x.adp if x.adp else 9999.0))
            # count my current roster by position from the pick log
            rc = {}
            for pl in pick_log:
                if pl[3] == my_slot:
                    rc[pl[2]] = rc.get(pl[2], 0) + 1
            pick = pick_for_roster(pvs, cfg, rc)
            if pick is None:
                break
            drafted.add(pick.name)
            team_rosters.setdefault(my_slot, []).append(pick.name)
            pick_log.append((ov, pick.name, pick.position, my_slot))
            ov += 1
        if ov == prev:            # stall guard — should never fire
            break

    mine = [(pl[0], pl[1], pl[2]) for pl in pick_log if pl[3] == my_slot]
    counts = {}
    for _, _, pos in mine:
        counts[pos] = counts.get(pos, 0) + 1
    return {"my_roster": mine, "pick_log": pick_log, "counts": counts}
