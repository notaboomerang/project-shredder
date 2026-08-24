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
    needs = open_needs(roster, cfg)
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

        composite = _composite(pv, vva, surv, needs, pv.name in last_in_tier,
                               raw, my_qb_teams, my_pass_catcher_teams,
                               prefer_floor)
        composite += INJ.composite_penalty(pv.name)
        # ROSTER-SURPLUS PENALTY: you already have enough of this position, so a
        # 2nd/3rd here is only bench/flex value — demote it hard, especially for
        # single-start slots (a 2nd TE/QB/DST/K should never top the board once
        # the starter is filled). This is what stops "you have a TE, draft 3 TEs".
        _have_pos = sum(1 for _n, _p in roster.players if _p == pv.position)
        _start = cfg.starters.get(pv.position, 0)
        _flex_room = pv.position in ("RB", "WR", "TE") and cfg.starters.get("FLEX", 0)
        if _have_pos >= _start:
            over = _have_pos - _start + 1          # how many past the starter need
            if pv.position in ("QB", "K", "DST"):
                composite -= 120.0 * over          # never stack single-start slots
            elif pv.position == "TE":
                # TE fills flex only marginally; 2nd TE eased, 3rd+ crushed
                composite -= (30.0 if (_flex_room and _have_pos == _start)
                              else 110.0 * over)
            else:  # RB / WR — flex + bench depth is fine, mild taper
                composite -= 8.0 * max(0, _have_pos - _start - 1)
        _inj = INJ.injury_for(pv.name)
        recs.append(Recommendation(
            name=pv.name, position=pv.position, team=pv.team,
            proj_points=round(pv.proj_points, 1), vorp=pv.vorp,
            pos_rank=pv.pos_rank, tier=pv.tier, adp=adp, value_vs_adp=vva,
            survival=surv, composite=round(composite, 1), badges=badges,
            injury_chip=(_inj.chip if _inj else None),
            injury_note=(_inj.narrative if _inj else None),
        ))

    recs.sort(key=lambda r: r.composite, reverse=True)
    return recs[:top_n]


def _overall_rank(pvs: list[E.PlayerValue], target: E.PlayerValue) -> int:
    ordered = sorted(pvs, key=lambda x: x.vorp, reverse=True)
    for i, pv in enumerate(ordered, start=1):
        if pv.name == target.name:
            return i
    return len(ordered)


def _composite(pv, vva, surv, needs, is_cliff, raw,
               my_qb_teams, my_pc_teams, prefer_floor=False) -> float:
    """Blend the edges into one number. VORP is the backbone; the rest nudge."""
    score = pv.vorp
    # positional-scarcity prior: QB/K/DST are streamable, so discount their
    # pool-relative VORP early (an empty board otherwise floats QBs to the top).
    # Survival badges already say "CAN WAIT"; this makes the SORT agree.
    _SCARCITY = {"RB": 1.0, "WR": 1.0, "TE": 0.9, "QB": 0.55, "K": 0.2, "DST": 0.25}
    score *= _SCARCITY.get(pv.position, 1.0)
    # need weighting: multiply value by how much we need the position
    need_w = 1.0 + 0.15 * min(needs.get(pv.position, 0), 2)
    score *= need_w
    # market: reward being a value vs ADP
    if vva is not None:
        # vva is now in POSITIONAL draft-slots (model pos-rank vs ADP pos-rank).
        score += 3.0 * max(-8, min(8, vva))
    # urgency: if he won't survive, nudge up so we grab now
    if surv is not None and surv <= 0.3:
        score += 8
    # tier cliff: don't let the last elite piece walk
    if is_cliff and pv.tier <= 3:
        score += 6
    # stack synergy bonus
    if (pv.position in ("WR", "TE") and raw.team in my_qb_teams) or \
       (pv.position == "QB" and raw.team in my_pc_teams):
        score += 5
    # schedule softness (pass game)
    if pv.position in ("QB", "WR", "TE"):
        rpt = M.schedule_report(raw.team)
        if rpt:
            score += 0.4 * rpt.season_softness + 0.6 * rpt.playoff_softness
        # venue / dome environment nudge
        vdelta, _ = VEN.venue_adjustment(raw.team, pv.position)
        score += vdelta
    # advanced metrics nudge (opportunity/role/environment/risk)
    mdelta, _ = ADV.metric_adjustments(pv.name, pv.position)
    score += mdelta
    # consistency: when the user wants "highest-scoring CONSISTENTLY", reward a
    # steady weekly floor and mildly fade boom/bust.
    if prefer_floor:
        cscore, _ = ADV.consistency_score(pv.name, pv.position)
        score += (cscore - 50) * 0.25
    # age
    if pv.position == "RB" and raw.age and raw.age >= 28:
        score -= 4
    if raw.rookie:
        score += 2
        # combine / athletic profile nudges rookie ceiling (RB/WR/TE only)
        adelta, _ = CMB.athletic_adjustment(pv.name, pv.position, raw.rookie)
        score += adelta
    return score
