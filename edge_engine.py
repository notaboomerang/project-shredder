"""
The Edge Engine — Level 100.

Fuses every value concept into one ranked recommendation per pick. Each player
gets a composite score and a set of human-readable EDGE BADGES so you draft on
the blend, not any single number.

Edges implemented:
  SCIENCE  - VORP (value over replacement, from engine.compute_vorp)
           - tier + tier-cliff detection ("last elite RB")
           - roster-need weighting (fills your open starting slots)
           - survival probability (will he last to your next pick? via ADP+gap)
  MARKET   - value vs ADP (VORP rank far ahead of ADP = steal; behind = reach)
  ART      - stack synergy (pairs with a QB/receiver already on your roster)
           - schedule softness (matchups.py: soft pass-D slate, playoff weeks)
           - game environment hook (Vegas total, when loaded)
  PROFILE  - age/breakout curve (RB age cliff ~28+, WR/TE youth breakout)
           - rookie upside flag
  RISK     - bye-week conflict with your current starters
           - handcuff / contingent-upside flag (hook)

Everything is a pure function of (player pool, league config, your roster,
drafted set, current overall pick). No global state; the UI calls recommend().
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import engine as E
import matchups as M
import projections as P
import opponents as _opponents_mod
import combine as CMB
import venue as VEN
import advanced_metrics as ADV
import injuries as INJ


# ---------------------------------------------------------------------------
# survival probability: will `adp` player last until my next pick?
# ---------------------------------------------------------------------------
def survival_probability(adp: Optional[float], picks_until_next: int,
                         current_overall: int) -> Optional[float]:
    """Rough logistic model: the further my next pick is beyond his ADP, the
    less likely he survives. Returns 0..1 (probability still available at my
    next turn). None if no ADP."""
    if adp is None or picks_until_next <= 0:
        return None
    my_next_overall = current_overall + picks_until_next
    # how many picks past his ADP is my next selection?
    delta = my_next_overall - adp
    # logistic: at delta=0 ~50%, spread ~ a few picks (ADP noise)
    prob_taken = 1.0 / (1.0 + math.exp(-delta / 3.0))
    return round(max(0.0, min(1.0, 1.0 - prob_taken)), 2)


# ---------------------------------------------------------------------------
# roster model
# ---------------------------------------------------------------------------
@dataclass
class Roster:
    """The user's current team. players = list of (name, position)."""
    players: list[tuple[str, str]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        c: dict[str, int] = {}
        for _, pos in self.players:
            c[pos] = c.get(pos, 0) + 1
        return c

    def names(self) -> set[str]:
        return {n for n, _ in self.players}

    def teams_of_positions(self, positions: tuple[str, ...]) -> set[str]:
        # used for stack detection — needs team, filled by recommend() via pool
        return set()


def open_needs(roster: Roster, cfg: E.LeagueConfig) -> dict[str, float]:
    """How badly each position is still needed for STARTING slots (incl flex).
    Returns a need-weight per position (higher = more needed)."""
    have = roster.counts()
    need: dict[str, float] = {}
    s = cfg.starters
    for pos in ("QB", "RB", "WR", "TE", "K", "DST"):
        dedicated = s.get(pos, 0)
        need[pos] = max(0, dedicated - have.get(pos, 0))
    # flex demand: if flex slots unfilled, boost RB/WR/TE
    flex_open = 0
    for slot, cnt in s.items():
        if slot in E.FLEX_ELIGIBLE:
            flex_open += cnt
    filled_flexable = sum(have.get(p, 0) for p in ("RB", "WR", "TE"))
    dedicated_flexable = sum(s.get(p, 0) for p in ("RB", "WR", "TE"))
    surplus = max(0, filled_flexable - dedicated_flexable)
    remaining_flex = max(0, flex_open - surplus)
    if remaining_flex:
        for p in ("RB", "WR", "TE"):
            need[p] = need.get(p, 0) + 0.4 * remaining_flex
    return need


def roster_state(roster: Roster, cfg: E.LeagueConfig) -> dict:
    """Single source of truth for 'what does my starting lineup still need'.

    Returns a dict the UI and the composite can both read:
      counts       {pos: n}                 how many of each I've drafted
      starters     {pos: n}                 dedicated starter slots for each pos
      filled       {pos: n}                 how many of my players fill a STARTER
                                            slot at that pos (capped at starters)
      flex_slots   int                      total FLEX slots (RB/WR/TE eligible)
      flex_filled  int                      flex slots I've already covered w/ surplus
      flex_open    int                      flex slots still open
      needs        {pos: float}             open-need weight (from open_needs)
      starter_open {pos: int}               dedicated starter slots still empty
      done         set[str]                 positions where I have zero remaining
                                            starter/flex value to add (pure bench)
    """
    have = roster.counts()
    s = cfg.starters
    flex_slots = sum(c for slot, c in s.items() if slot in E.FLEX_ELIGIBLE)

    # dedicated starter fill per position (capped at the slot count)
    filled = {p: min(have.get(p, 0), s.get(p, 0))
              for p in ("QB", "RB", "WR", "TE", "K", "DST")}
    starter_open = {p: max(0, s.get(p, 0) - have.get(p, 0))
                    for p in ("QB", "RB", "WR", "TE", "K", "DST")}

    # flex is filled by RB/WR/TE beyond their dedicated starter slots
    flex_surplus = sum(max(0, have.get(p, 0) - s.get(p, 0))
                       for p in ("RB", "WR", "TE"))
    flex_filled = min(flex_slots, flex_surplus)
    flex_open = max(0, flex_slots - flex_filled)

    needs = open_needs(roster, cfg)

    # a position is "done" (bench-only from here) when its dedicated starter
    # slots are full AND there's no flex room it can still fill. Single-start
    # slots (QB/K/DST) are done the moment they're filled.
    done: set[str] = set()
    for p in ("QB", "RB", "WR", "TE", "K", "DST"):
        if starter_open.get(p, 0) > 0:
            continue
        if p in ("RB", "WR", "TE") and flex_open > 0:
            continue
        done.add(p)

    return {
        "counts": have, "starters": dict(s), "filled": filled,
        "flex_slots": flex_slots, "flex_filled": flex_filled,
        "flex_open": flex_open, "needs": needs,
        "starter_open": starter_open, "done": done,
    }


# ---------------------------------------------------------------------------
# the recommendation
# ---------------------------------------------------------------------------
@dataclass
class Recommendation:
    name: str
    position: str
    team: str
    proj_points: float
    vorp: float
    pos_rank: int
    tier: int
    adp: Optional[float]
    value_vs_adp: Optional[float]
    survival: Optional[float]
    composite: float
    badges: list[str] = field(default_factory=list)
    injury_chip: Optional[str] = None
    injury_note: Optional[str] = None
    # itemized "why this pick" breakdown: list of dicts with keys
    #   label   short name of the factor
    #   value   the numeric contribution to the composite (signed), or None for
    #           context-only rows (e.g. raw ADP, bye week)
    #   detail  a plain-English explanation of what the factor means
    explain: list[dict] = field(default_factory=list)


def _age_badge(pos: str, age: Optional[float], rookie: bool) -> Optional[str]:
    if rookie:
        return "ROOKIE upside"
    if age is None:
        return None
    if pos == "RB" and age >= 28:
        return f"AGE risk (RB {int(age)})"
    if pos in ("WR", "TE") and age <= 25:
        return "BREAKOUT window"
    if pos == "QB" and age >= 37:
        return f"AGE risk (QB {int(age)})"
    return None


def recommend(pool: list[P.RawPlayer], cfg: E.LeagueConfig, roster: Roster,
              drafted: set[str], current_overall: int,
              scoring_key: str = "half", top_n: int = 40,
              opponents=None, prefer_floor: bool = False) -> list[Recommendation]:
    """Rank the best available players by the blended edge composite."""
    # 1) score projections for THIS scoring format -> PlayerValue list
    name_to_raw = {p.name: p for p in pool}
    pvs: list[E.PlayerValue] = []
    for p in pool:
        if p.name in drafted:
            continue
        pts = E.project_points(p.stats, cfg.scoring)
        pvs.append(E.PlayerValue(p.name, p.name, p.position, p.team, pts))
    if not pvs:
        return []

    # 2) VORP + tiers from the CURRENTLY AVAILABLE pool (dynamic baseline)
    E.compute_vorp(pvs, cfg)

    # POSITION-RELATIVE value basis (full-field, stable). "Value vs ADP" should
    # answer: does the model rank this player higher AT HIS POSITION than the
    # market does? That's the roster-construction lens ADP actually uses — and it
    # is honest for QB/K/DST where cross-position raw points mislead (an elite
    # 1-QB-league QB is the market's QB1 and the model's QB1 -> neutral, not a
    # phantom steal). Computed over ALL players so it never drifts as the pool
    # drains. Fixes both the depleted-pool inflation and the elite-QB illusion.
    _model_pos_rank: dict[str, int] = {}
    _adp_pos_rank: dict[str, int] = {}
    _by_pos_pts: dict[str, list] = {}
    _by_pos_adp: dict[str, list] = {}
    for p in pool:
        _pts = E.project_points(p.stats, cfg.scoring)
        _by_pos_pts.setdefault(p.position, []).append((p.name, _pts))
        _a = P.adp_for(p, scoring_key)
        if _a:
            _by_pos_adp.setdefault(p.position, []).append((p.name, _a))
    for _lst in _by_pos_pts.values():
        for _i, (_nm, _) in enumerate(sorted(_lst, key=lambda x: x[1], reverse=True), 1):
            _model_pos_rank[_nm] = _i
    for _lst in _by_pos_adp.values():
        for _i, (_nm, _) in enumerate(sorted(_lst, key=lambda x: x[1]), 1):
            _adp_pos_rank[_nm] = _i

    # tier-cliff detection: is this the last player in his tier at his position?
    last_in_tier: set[str] = set()
    by_pos: dict[str, list[E.PlayerValue]] = {}
    for pv in pvs:
        by_pos.setdefault(pv.position, []).append(pv)
    for plist in by_pos.values():
        plist.sort(key=lambda x: x.vorp, reverse=True)
        for i, pv in enumerate(plist):
            nxt = plist[i + 1] if i + 1 < len(plist) else None
            if nxt is None or nxt.tier != pv.tier:
                last_in_tier.add(pv.name)

    # 3) roster need + my pick timing
    rstate = roster_state(roster, cfg)
    needs = rstate["needs"]
    my_picks = cfg.my_overall_picks()
    picks_until_next = 0
    my_next_overall = None
    for op in my_picks:
        if op > current_overall:
            picks_until_next = op - current_overall
            my_next_overall = op
            break

    # my roster's teams for stack detection
    my_qb_teams = {name_to_raw[n].team for n, pos in roster.players
                   if pos == "QB" and n in name_to_raw}
    my_pass_catcher_teams = {name_to_raw[n].team for n, pos in roster.players
                             if pos in ("WR", "TE") and n in name_to_raw}
    roster_byes = {name_to_raw[n].bye for n, _ in roster.players
                   if n in name_to_raw and name_to_raw[n].bye}

    # ---- DYNAMIC STREAMER GATE (DST/K) --------------------------------------
    # DST and K are pure streamers: they should stay OFF the board until the
    # draft signals it's time, so a noisy preseason projection can't float a
    # defense into round 3. A position "opens" (its players rank on real value)
    # when EITHER trigger fires:
    #   • RUN STARTED  — enough of that position already drafted leaguewide, so
    #     grabbing a good one now is legitimate (don't get left with scraps).
    #   • ADP REACHED  — we're within a window of the best-available player's
    #     actual ADP, i.e. they'd realistically come off the board around now.
    # Until then their composite is clamped BELOW any real need so they never
    # out-rank a startable skill player. This replaces the old static ceiling.
    _stream_gate: dict[str, bool] = {}
    _RUN_TRIGGER = {"DST": 2, "K": 2}     # this many gone leaguewide = run is on
    _ADP_WINDOW = int(cfg.teams)          # within ~1 round of ADP = "time"
    for _pos in ("DST", "K"):
        _gone = sum(1 for n in drafted
                    if n in name_to_raw and name_to_raw[n].position == _pos)
        _avail_adps = [P.adp_for(name_to_raw[pv.name], scoring_key)
                       for pv in pvs if pv.position == _pos]
        _avail_adps = [a for a in _avail_adps if a]
        _best_adp = min(_avail_adps) if _avail_adps else None
        run_on = _gone >= _RUN_TRIGGER.get(_pos, 2)
        adp_reached = (_best_adp is not None
                       and current_overall >= _best_adp - _ADP_WINDOW)
        _stream_gate[_pos] = bool(run_on or adp_reached)

    recs: list[Recommendation] = []
    for pv in pvs:
        raw = name_to_raw[pv.name]
        adp = P.adp_for(raw, scoring_key)
        _mpr = _model_pos_rank.get(pv.name)
        _apr = _adp_pos_rank.get(pv.name)
        vva = round(float(_apr - _mpr), 1) if (adp and _mpr and _apr) else None
        if opponents is not None and my_next_overall is not None:
            surv = _opponents_mod.opponent_aware_survival(
                adp, pv.position, raw.team, raw.rookie,
                current_overall, my_next_overall, opponents)
        else:
            surv = survival_probability(adp, picks_until_next, current_overall)

        badges: list[str] = []
        # MARKET: value vs ADP
        if vva is not None and vva >= 4:
            badges.append(f"VALUE +{int(vva)} at {pv.position} vs ADP")
        elif vva is not None and vva <= -4:
            badges.append(f"REACH {int(vva)} at {pv.position} vs ADP")
        # SCIENCE: tier cliff + need + survival
        if pv.name in last_in_tier and pv.tier <= 3:
            badges.append(f"TIER CLIFF (last T{pv.tier} {pv.position})")
        if needs.get(pv.position, 0) >= 1:
            badges.append(f"FILLS NEED ({pv.position})")
        if surv is not None and surv <= 0.25 and picks_until_next > 0:
            badges.append(f"WON'T LAST ({int(surv*100)}% to next pick)")
        elif surv is not None and surv >= 0.75 and picks_until_next > 0:
            badges.append(f"CAN WAIT ({int(surv*100)}% survives)")
        # ART: stack synergy with my roster
        if pv.position in ("WR", "TE") and raw.team in my_qb_teams:
            badges.append("STACKS w/ your QB")
        if pv.position == "QB" and raw.team in my_pass_catcher_teams:
            badges.append("STACKS w/ your WR/TE")
        # ART: schedule softness (pass game for QB/WR/TE)
        if pv.position in ("QB", "WR", "TE"):
            rpt = M.schedule_report(raw.team)
            if rpt and rpt.grade.startswith(("A", "B")):
                badges.append(f"SOFT PASS SLATE ({rpt.grade[0]})")
        # ART: venue / dome environment
        _vdelta, _vbadge = VEN.venue_adjustment(raw.team, pv.position)
        if _vbadge:
            badges.append(_vbadge)
        # RISK CONTEXT: turf home field (soft-tissue injury risk) — RB/WR/TE
        # carry the lower-body load. Informational badge ONLY; never folded into
        # the composite (turf is a durability caveat, not a value change).
        if pv.position in ("RB", "WR", "TE"):
            _turf = VEN.turf_exposure(raw.team)
            if _turf and _turf.get("flag"):
                badges.append(_turf["flag"])
        # PROFILE: advanced metrics (opportunity / role / environment / risk)
        _mdelta, _mbadges = ADV.metric_adjustments(pv.name, pv.position)
        badges.extend(_mbadges[:3])   # cap to keep the row readable
        # consistency / floor
        _cscore, _cbadge = ADV.consistency_score(pv.name, pv.position)
        if _cbadge:
            badges.append(_cbadge)
        # PROFILE: age/breakout/rookie
        ab = _age_badge(pv.position, raw.age, raw.rookie)
        if ab:
            badges.append(ab)
        # PROFILE: combine / athletic profile (rookie RB/WR/TE only)
        _adelta, _abadge = CMB.athletic_adjustment(pv.name, pv.position, raw.rookie)
        if _abadge:
            badges.append(_abadge)
        # RISK: bye conflict
        if raw.bye and raw.bye in roster_byes:
            badges.append(f"BYE clash (wk {raw.bye})")

        explain: list[dict] = []
        # context rows first (no score value; pure information)
        explain.append({"label": "Projected points", "value": None,
                        "detail": f"{round(pv.proj_points, 1)} pts for your scoring "
                        f"format · position rank {pv.position}{pv.pos_rank}, tier "
                        f"T{pv.tier}."})
        if adp is not None:
            explain.append({"label": "ADP", "value": None,
                            "detail": f"Average draft position {adp} ({scoring_key} "
                            f"consensus). You're on the clock at overall "
                            f"{current_overall}."})
        if surv is not None and picks_until_next > 0:
            explain.append({"label": "Survival to next pick", "value": None,
                            "detail": f"{int(surv*100)}% chance he's still available "
                            f"at your next pick (#{my_next_overall}, "
                            f"{picks_until_next} picks away)."})
        if raw.bye:
            explain.append({"label": "Bye week", "value": None,
                            "detail": f"Week {raw.bye}"
                            + (" — clashes with a current starter's bye."
                               if raw.bye in roster_byes else ".")})

        composite = _composite(pv, vva, surv, needs, pv.name in last_in_tier,
                               raw, my_qb_teams, my_pass_catcher_teams,
                               prefer_floor, explain=explain)
        _injpen = INJ.composite_penalty(pv.name)
        composite += _injpen
        if _injpen:
            _inj0 = INJ.injury_for(pv.name)
            explain.append({"label": "Injury penalty", "value": round(_injpen, 1),
                            "detail": (_inj0.narrative if _inj0 and _inj0.narrative
                                       else "Current injury designation reduces "
                                       "confidence in the projection.")})
        # ROSTER-SURPLUS PENALTY — the fix for "you have a TE, it keeps saying
        # draft another TE." Once a position can no longer improve your STARTING
        # lineup (dedicated starter slots full + no flex room it can fill), any
        # further player there is pure bench depth and must rank BELOW every
        # player who still fills a starting slot. The old code used flat additive
        # penalties (e.g. -30 for a 2nd TE) that an elite VORP could out-run, so
        # a great backup TE still floated to the top. We instead COLLAPSE the
        # surplus player's score toward bench value: strip its VORP-driven score
        # and replace it with a small depth-only signal, guaranteeing it can
        # never leapfrog a real need. Depth at RB/WR (where flex/bye depth truly
        # matters) keeps a little more value than a stockpiled QB/TE/K/DST.
        _pre_surplus = composite
        composite = _apply_surplus(composite, pv, roster, cfg, rstate)
        if composite != _pre_surplus:
            explain.append({"label": "Roster surplus", "value":
                            round(composite - _pre_surplus, 1),
                            "detail": f"You've already filled your starting "
                            f"{pv.position} slot(s), so another {pv.position} is "
                            f"only bench/flex depth — its score is capped below "
                            f"any player who still fills a starting need."})
        # DYNAMIC STREAMER GATE: while DST/K is still "closed" (no run started
        # and we're well before its ADP), bury its composite so it can't jump a
        # real pick. Once the gate opens (run on OR ADP reached) it ranks on
        # merit like any other position.
        if pv.position in _stream_gate and not _stream_gate[pv.position]:
            _pre_gate = composite
            composite = min(composite, -50.0)   # parked below every startable player
            if composite != _pre_gate:
                explain.append({"label": "Stream later", "value":
                                round(composite - _pre_gate, 1),
                                "detail": f"{pv.position} is a streamer — held "
                                f"back until a run starts or you reach its draft "
                                f"range, so it won't crowd out a real pick early."})
        _inj = INJ.injury_for(pv.name)
        recs.append(Recommendation(
            name=pv.name, position=pv.position, team=pv.team,
            proj_points=round(pv.proj_points, 1), vorp=pv.vorp,
            pos_rank=pv.pos_rank, tier=pv.tier, adp=adp, value_vs_adp=vva,
            survival=surv, composite=round(composite, 1), badges=badges,
            injury_chip=(_inj.chip if _inj else None),
            injury_note=(_inj.narrative if _inj else None),
            explain=explain,
        ))

    recs.sort(key=lambda r: r.composite, reverse=True)
    return recs[:top_n]


def _overall_rank(pvs: list[E.PlayerValue], target: E.PlayerValue) -> int:
    ordered = sorted(pvs, key=lambda x: x.vorp, reverse=True)
    for i, pv in enumerate(ordered, start=1):
        if pv.name == target.name:
            return i
    return len(ordered)


def _apply_surplus(composite: float, pv, roster: Roster, cfg: E.LeagueConfig,
                   rstate: dict) -> float:
    """Demote players at positions that can no longer help your STARTING lineup.

    A position is 'saturated' when its dedicated starter slots are full and, for
    flex-eligible positions, no flex slot remains for it. A saturated player is
    worth only bench/depth value, so we DON'T just subtract a flat number (an
    elite VORP would survive it) — we REBASE the score to a small depth value
    that always sits below any player still filling a real need.
    """
    pos = pv.position
    have = rstate["counts"].get(pos, 0)
    start = rstate["starters"].get(pos, 0)
    flex_open = rstate["flex_open"]
    flex_eligible = pos in ("RB", "WR", "TE")

    # can this position still fill a STARTER or FLEX slot? if so, no surplus.
    starter_open = max(0, start - have)
    if starter_open > 0:
        return composite
    if flex_eligible and flex_open > 0:
        # still has a flex home — treat as needed but slightly discounted so a
        # genuine dedicated-starter need edges it out.
        return composite - 6.0

    # SATURATED: pure bench depth from here. Rebase to a depth-only score.
    # how many bodies past the last useful (starter+flex) slot are we?
    useful_cap = start + (1 if flex_eligible else 0)
    depth_index = max(1, have - useful_cap + 1)   # 1 for the first true backup

    # base bench value: keep a whisper of the player's quality so the best
    # backup at a position still sorts above a scrub, but cap it low.
    quality = min(6.0, max(0.0, pv.vorp) * 0.15)
    if pos in ("RB", "WR"):
        bench_value = 4.0 + quality        # depth here matters (bye/injury/flex churn)
    elif pos == "TE":
        bench_value = -6.0 + quality * 0.5  # a 2nd TE is rarely worth a pick early
    else:  # QB / K / DST — never stockpile; a 2nd is nearly worthless mid-draft
        bench_value = -40.0

    # each extra body beyond the first backup drops further
    bench_value -= 8.0 * (depth_index - 1)
    # never let a surplus player score higher than the useful ceiling of a needed
    # one; clamp to a low band regardless of raw VORP.
    return min(composite, bench_value)


_SCARCITY = {"RB": 1.0, "WR": 1.0, "TE": 0.9, "QB": 0.55, "K": 0.2, "DST": 0.25}


def _composite(pv, vva, surv, needs, is_cliff, raw,
               my_qb_teams, my_pc_teams, prefer_floor=False,
               explain: Optional[list] = None) -> float:
    """Blend the edges into one number. VORP is the backbone; the rest nudge.

    If `explain` (a list) is passed, each factor appends an itemized row
    {label, value, detail} so the UI can show EXACTLY what drove the score.
    """
    def _add(label, value, detail):
        if explain is not None:
            explain.append({"label": label, "value": value, "detail": detail})

    # --- backbone: VORP, scaled by positional scarcity ---
    scarcity = _SCARCITY.get(pv.position, 1.0)
    score = pv.vorp * scarcity
    if scarcity != 1.0:
        _add("VORP × scarcity", round(score, 1),
             f"Value over replacement ({pv.vorp}) × {scarcity:g} scarcity prior "
             f"— {pv.position} is streamable, so its raw VORP is discounted early.")
    else:
        _add("VORP", round(score, 1),
             f"Value over replacement: projected points above a replacement-level "
             f"{pv.position} in your league. The backbone of the score.")

    # --- roster need multiplier ---
    need_val = needs.get(pv.position, 0)
    need_w = 1.0 + 0.15 * min(need_val, 2)
    if need_w != 1.0:
        before = score
        score *= need_w
        _add("Roster need", round(score - before, 1),
             f"You still need {pv.position} for a starting slot — value boosted "
             f"×{need_w:.2f}.")

    # --- market: value vs ADP ---
    if vva is not None:
        delta = 3.0 * max(-8, min(8, vva))
        score += delta
        if abs(vva) >= 1:
            kind = "steal (model ranks him ahead of the market)" if vva > 0 \
                else "reach (market ranks him ahead of the model)"
            _add("Value vs ADP", round(delta, 1),
                 f"Model has him {abs(int(vva))} spot(s) "
                 f"{'ahead of' if vva > 0 else 'behind'} his ADP at {pv.position} "
                 f"— {kind}.")

    # --- urgency: won't survive to your next pick ---
    if surv is not None and surv <= 0.3:
        score += 8
        _add("Won't last", 8.0,
             f"Only {int(surv*100)}% chance he's still here at your next pick — "
             f"grab-now urgency.")

    # --- tier cliff: last elite piece at the position ---
    if is_cliff and pv.tier <= 3:
        score += 6
        _add("Tier cliff", 6.0,
             f"He's the last player in tier T{pv.tier} at {pv.position} — a real "
             f"drop-off follows, so there's urgency to take him now.")

    # --- stack synergy with your roster ---
    if (pv.position in ("WR", "TE") and raw.team in my_qb_teams) or \
       (pv.position == "QB" and raw.team in my_pc_teams):
        score += 5
        _add("Stack synergy", 5.0,
             f"Pairs with a player you already roster on {raw.team} — correlated "
             f"scoring upside.")

    # --- schedule softness + venue (pass game) ---
    if pv.position in ("QB", "WR", "TE"):
        rpt = M.schedule_report(raw.team)
        if rpt:
            sdelta = 0.4 * rpt.season_softness + 0.6 * rpt.playoff_softness
            if abs(sdelta) >= 0.05:
                score += sdelta
                _add("Schedule (pass D)", round(sdelta, 1),
                     f"Strength of pass-defense schedule (grade {rpt.grade}), "
                     f"weighted toward the fantasy playoff weeks.")
        vdelta, vbadge = VEN.venue_adjustment(raw.team, pv.position)
        if vdelta:
            score += vdelta
            _add("Venue / environment", round(vdelta, 1),
                 vbadge or f"Home-venue adjustment for {raw.team}.")

    # --- advanced metrics (opportunity / role / environment / risk) ---
    mdelta, mbadges = ADV.metric_adjustments(pv.name, pv.position)
    if mdelta:
        score += mdelta
        _add("Advanced metrics", round(mdelta, 1),
             ("; ".join(mbadges[:3]) if mbadges
              else "Opportunity/role/environment signals."))

    # --- consistency (only when prioritizing weekly floor) ---
    if prefer_floor:
        cscore, cbadge = ADV.consistency_score(pv.name, pv.position)
        cdelta = (cscore - 50) * 0.25
        if abs(cdelta) >= 0.1:
            score += cdelta
            _add("Consistency", round(cdelta, 1),
                 cbadge or f"Weekly-floor consistency score {cscore}/100 "
                 f"(you asked to prioritize floor).")

    # --- age / rookie profile ---
    if pv.position == "RB" and raw.age and raw.age >= 28:
        score -= 4
        _add("Age risk", -4.0,
             f"RB aged {int(raw.age)} — past the typical production cliff.")
    if raw.rookie:
        score += 2
        _add("Rookie upside", 2.0, "Rookie — undervalued ceiling this early.")
        adelta, abadge = CMB.athletic_adjustment(pv.name, pv.position, raw.rookie)
        if adelta:
            score += adelta
            _add("Athletic profile", round(adelta, 1),
                 abadge or "Combine/athletic testing adjustment.")
    return score
