"""
Trade Facilitator — turn roster imbalance (and dead weight) into win-win trades.

The problem this solves: a per-player projection tells you who's good; it does
NOT tell you that your RB2 slot is a sinkhole while you're three-deep at WR, or
that your round-1 RB just tore his ACL and is a zero every week. A trade is only
worth proposing if it (a) fixes a REAL hole on YOUR starting lineup and (b) is
"not garbage" for the other manager — i.e. it also fixes a real hole on THEIRS.

Design (follows KC's "identify strengths and weaknesses, then go from there"):

  1. profile_team()      For every team: per-position SURPLUS (startable VORP
                         sitting on the bench = tradeable depth) and DEFICIT
                         (open starter/flex slots × the value gap to fill them).
                         Injured-OUT / season-ending players are flagged as
                         FROZEN VALUE — high nominal value you cannot deploy, so
                         they're a dead roster spot and a prime sell asset.

  2. find_trades()       Match MY surplus→MY deficit against an opponent's
                         surplus→their deficit. Score by MUTUAL lineup gain:
                         both my delta AND their delta must be positive (a true
                         win-win), inside a fairness band so I'm not fleecing
                         them (a lopsided deal gets vetoed in real leagues).

  3. Draft-history read  opponents.learn_from_history tags each manager
                         (RB-heavy, zero-RB, QB-early, …) so the pitch reads
                         true to how they actually draft and what they value.

Everything is INFORMATIONAL and a pure function of (rosters, pool, cfg). No
projection/VORP mutation, honoring the no-artificial-value rule. Values come
from engine.compute_vorp so they're consistent with the rest of the app.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import engine as E
import projections as P
import edge_engine as EE
import opponents as OPP


# Injury statuses that mean "this player returns ~zero this season" — a dead
# roster spot. ESPN injuryStatus strings. OUT alone is week-to-week, but a
# season-ending designation (IR / torn ACL etc.) is handled by the caller
# passing season_ending names explicitly; we also treat these as frozen.
_FROZEN_STATUSES = {"OUT", "INJURY_RESERVE", "IR", "SUSPENSION", "OUT_FOR_SEASON"}

# Positions we actually trade on (K/DST are stream/replace, not trade assets).
_TRADE_POS = ("QB", "RB", "WR", "TE")


# ---------------------------------------------------------------------------
# per-player value, keyed by name, from the project's own VORP
# ---------------------------------------------------------------------------
def value_index(pool, cfg: E.LeagueConfig) -> dict[str, E.PlayerValue]:
    """Compute VORP for the FULL pool once and index it by player name. This is
    the single valuation both sides of every trade are priced against."""
    pvs: list[E.PlayerValue] = []
    for p in pool:
        try:
            pts = E.project_points(p.stats, cfg.scoring)
        except Exception:
            continue
        pvs.append(E.PlayerValue(p.name, p.name, p.position, p.team, pts))
    E.compute_vorp(pvs, cfg)
    return {pv.name: pv for pv in pvs}


# ---------------------------------------------------------------------------
# 1. TEAM PROFILE — strengths (surplus) & weaknesses (deficit)
# ---------------------------------------------------------------------------
@dataclass
class Asset:
    name: str
    position: str
    vorp: float
    frozen: bool = False          # injured-out / season-ending = can't deploy
    frozen_reason: str = ""


@dataclass
class TeamProfile:
    team_id: object
    team_name: str
    is_mine: bool
    # per-position lists of bench-surplus assets (startable value you don't need)
    surplus: dict[str, list[Asset]] = field(default_factory=dict)
    # per-position deficit weight (higher = bigger hole in the STARTING lineup)
    deficit: dict[str, float] = field(default_factory=dict)
    # dead roster spots (frozen players) — prime sell/replace targets
    frozen: list[Asset] = field(default_factory=list)
    tendencies: list[str] = field(default_factory=lambda: ["ADP-robot"])
    counts: dict[str, int] = field(default_factory=dict)

    def strongest(self) -> list[str]:
        """Positions ranked by total surplus VORP (your trade chips)."""
        tot = {pos: sum(a.vorp for a in lst) for pos, lst in self.surplus.items()}
        return [p for p, _ in sorted(tot.items(), key=lambda kv: kv[1],
                                     reverse=True) if tot[p] > 0]

    def weakest(self) -> list[str]:
        """Positions ranked by deficit weight (your biggest holes)."""
        return [p for p, _ in sorted(self.deficit.items(), key=lambda kv: kv[1],
                                     reverse=True) if self.deficit[p] > 0]


def _starter_count(cfg: E.LeagueConfig, pos: str) -> int:
    return int(cfg.starters.get(pos, 0))


def profile_team(team: dict, vidx: dict[str, E.PlayerValue], cfg: E.LeagueConfig,
                 season_ending: Optional[set[str]] = None,
                 tendencies: Optional[list[str]] = None) -> TeamProfile:
    """Build a strength/weakness profile for one team.

    team      = {'team_id','team_name','is_mine','players':[{name,position,
                 injury}...]} (shape from espn.team_rosters()).
    vidx      = name -> PlayerValue (from value_index()).
    season_ending = names known to be done for the year (e.g. Achane torn ACL),
                 treated as frozen even if ESPN still shows a soft status.
    """
    season_ending = season_ending or set()
    players = team.get("players", []) or []

    # bucket this team's players by position, carrying value + frozen flag
    by_pos: dict[str, list[Asset]] = defaultdict(list)
    frozen: list[Asset] = []
    counts: dict[str, int] = defaultdict(int)
    for pl in players:
        name = pl.get("name")
        pos = pl.get("position")
        if not name or pos not in _TRADE_POS:
            continue
        counts[pos] += 1
        pv = vidx.get(name)
        vorp = round(pv.vorp, 1) if pv else 0.0
        inj = (pl.get("injury") or "").upper()
        is_frozen = name in season_ending or inj in _FROZEN_STATUSES
        reason = ""
        if name in season_ending:
            reason = "season-ending injury (torn ACL / IR)"
        elif inj in _FROZEN_STATUSES:
            reason = f"ESPN status: {inj}"
        a = Asset(name, pos, vorp, is_frozen, reason)
        by_pos[pos].append(a)
        if is_frozen:
            frozen.append(a)

    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: x.vorp, reverse=True)

    # ---- DEFICIT: open starter + flex slots, weighted by the value gap -----
    # A frozen player does NOT count toward filling a starter slot (he's a zero),
    # so his slot re-opens as a deficit — that's how Achane's dead R1 spot
    # becomes a weakness the tool wants to fix.
    live_counts: dict[str, int] = defaultdict(int)
    for pos in _TRADE_POS:
        live = sum(1 for a in by_pos.get(pos, []) if not a.frozen)
        live_counts[pos] = live

    rstate = _roster_state_from_counts(live_counts, cfg)
    deficit: dict[str, float] = {}
    for pos in _TRADE_POS:
        open_starter = rstate["starter_open"].get(pos, 0)
        # value gap: how far the team's current best DEPLOYABLE option at this
        # position is below a startable baseline. Deeper hole => bigger weight.
        deployable = [a.vorp for a in by_pos.get(pos, []) if not a.frozen]
        best = max(deployable) if deployable else -999.0
        # a startable player clears ~0 VORP; a hole where best is negative is worse
        gap = max(0.0, -best) if best < 0 else 0.0
        weight = open_starter * 10.0 + gap
        # flex pressure: thin at RB/WR/TE overall adds mild deficit
        if pos in ("RB", "WR", "TE") and rstate["flex_open"] > 0 and live_counts[pos] <= _starter_count(cfg, pos):
            weight += 3.0 * rstate["flex_open"]
        if weight > 0:
            deficit[pos] = round(weight, 1)

    # ---- SURPLUS: deployable depth beyond what the lineup needs ------------
    surplus: dict[str, list[Asset]] = {}
    for pos in _TRADE_POS:
        need = _starter_count(cfg, pos)
        if pos in ("RB", "WR", "TE"):
            need += 1  # keep one for flex/injury insurance before calling it surplus
        deployable = [a for a in by_pos.get(pos, []) if not a.frozen]
        extra = deployable[need:]
        extra = [a for a in extra if a.vorp > 0]  # only startable-grade is a chip
        if extra:
            surplus[pos] = extra

    return TeamProfile(
        team_id=team.get("team_id"),
        team_name=team.get("team_name", "Team"),
        is_mine=bool(team.get("is_mine")),
        surplus=surplus, deficit=deficit, frozen=frozen,
        tendencies=tendencies or ["ADP-robot"],
        counts=dict(counts),
    )


def _roster_state_from_counts(counts: dict[str, int], cfg: E.LeagueConfig) -> dict:
    """Lightweight roster_state over raw counts (edge_engine.roster_state wants a
    Roster of (name,pos); we only need the slot math here)."""
    roster = EE.Roster(players=[(f"_{pos}{i}", pos)
                                for pos, n in counts.items()
                                for i in range(n)])
    return EE.roster_state(roster, cfg)


# ---------------------------------------------------------------------------
# 2. TRADE MATCHING — win-win only
# ---------------------------------------------------------------------------
@dataclass
class TradeProposal:
    partner_team: str
    give: list[Asset]          # players I send
    get: list[Asset]           # players I receive
    my_delta: float            # my STARTING-lineup VORP gain
    their_delta: float         # partner's STARTING-lineup VORP gain
    fairness: float            # value I GET ÷ value I GIVE. 1.0 = even; >1 I come out ahead; <1 they do
    pitch: str = ""            # plain-English why THEY say yes
    rationale: str = ""        # why it helps ME


def _lineup_gain(profile: TeamProfile, give: list[Asset], get: list[Asset],
                 cfg: E.LeagueConfig) -> float:
    """Approximate change in a team's STARTING-lineup VORP from a give/get swap.

    Receiving a player at a DEFICIT position is worth his full VORP (he steps
    into an empty/weak starter slot). Giving away a SURPLUS player costs only the
    bench value you lose (near zero if he wasn't starting), so shedding surplus
    for need is strongly positive. Giving away a player at a position you NEED
    costs his full value.
    """
    gain = 0.0
    for a in get:
        if a.frozen:
            continue                        # a frozen player you RECEIVE is a zero
        hole = profile.deficit.get(a.position, 0.0)
        # full value if it fills a real hole; a fraction otherwise (bench depth)
        gain += a.vorp if hole > 0 else a.vorp * 0.25
    for a in give:
        is_surplus = any(s.name == a.name for s in profile.surplus.get(a.position, []))
        if a.frozen:
            cost = 0.0                      # a frozen player contributes nothing
        elif is_surplus:
            cost = a.vorp * 0.25            # only lose his bench/flex value
        else:
            cost = a.vorp                   # giving away a real contributor hurts
        gain -= cost
    return round(gain, 1)


def find_trades(me: TeamProfile, opponents: list[TeamProfile], cfg: E.LeagueConfig,
                max_per_partner: int = 2, fairness_band: tuple[float, float] = (0.55, 1.8),
                min_mutual: float = 2.0,
                allow_two_for_one: bool = True) -> list[TradeProposal]:
    """Generate win-win proposals: 1-for-1 and (when allow_two_for_one) 2-for-1
    consolidation packages.

    A proposal is kept only when BOTH my_delta and their_delta are positive (it
    improves each side's starting lineup) AND the VALUE exchanged is reasonably
    balanced — the players I send aren't drastically more/less valuable than
    those I get. Fairness is measured on raw player VORP, NOT the lineup-fit
    deltas (those are asymmetric by design: each side prizes the incoming piece
    by its OWN hole, so a mutually great deal can still be 2:1 on fit).

    The 2-for-1 pass is built for CONSOLIDATION: package a surplus player PLUS a
    dead roster spot (a frozen/injured player) to pry loose one stud at your
    need. The frozen piece costs you nothing but satisfies leagues that require
    even player counts, and lets you turn two bench bodies into one starter.

    Sorted by combined mutual gain, best first.
    """
    out: list[TradeProposal] = []
    my_needs = me.weakest()
    if not my_needs and not me.frozen:
        return out  # nothing to fix

    def _evaluate(opp, give, get):
        """Gate + score one give/get candidate. Returns a TradeProposal or None."""
        my_delta = _lineup_gain(me, give, get, cfg)
        their_delta = _lineup_gain(opp, get, give, cfg)
        if my_delta <= 0 or their_delta <= 0:
            return None
        if my_delta + their_delta < min_mutual:
            return None
        # fairness on RAW value exchanged: what I give up vs what I get back. A
        # frozen player I send is ~valueless to BOTH sides (the opponent knows
        # he's hurt), so he's priced at 0 in the fairness check — he's a
        # roster-count filler, not value. Live pieces carry their market vorp.
        give_val = sum(a.vorp for a in give if not a.frozen) or 0.1
        get_val = sum(a.vorp for a in get) or 0.1
        ratio = get_val / give_val
        if not (fairness_band[0] <= ratio <= fairness_band[1]):
            return None
        return _make_proposal(me, opp, give, get, my_delta, their_delta, ratio)

    for opp in opponents:
        found_for_partner: list[TradeProposal] = []
        # I want: a player at one of MY deficit positions, from THEIR surplus.
        # They want: a player at one of THEIR deficit positions, from MY surplus.
        for my_need in (my_needs or []):
            their_chips_here = opp.surplus.get(my_need, [])
            for get_asset in their_chips_here[:3]:
                # ---- 1-for-1 ----
                for their_need in opp.weakest():
                    my_chips_here = me.surplus.get(their_need, [])
                    for give_asset in my_chips_here[:3]:
                        pr = _evaluate(opp, [give_asset], [get_asset])
                        if pr:
                            found_for_partner.append(pr)

                # ---- 2-for-1 CONSOLIDATION: surplus (+ throw-in) for a stud ----
                # Only worth it when the target clearly out-values any single
                # chip I have (otherwise a 1-for-1 is strictly better for me).
                if allow_two_for_one:
                    for their_need in opp.weakest():
                        anchors = me.surplus.get(their_need, [])[:3]
                        for anchor in anchors:
                            # throw-in candidates: a frozen dead-spot (free), or a
                            # second lower surplus piece. Prefer the frozen one.
                            throwins = list(me.frozen)
                            # a second surplus piece at any position (not the anchor)
                            for pos2, lst in me.surplus.items():
                                for extra in lst:
                                    if extra.name != anchor.name:
                                        throwins.append(extra)
                            for ti in throwins[:4]:
                                if ti.name == anchor.name:
                                    continue
                                give = [anchor, ti]
                                # skip if the single anchor already clears value
                                # parity (then it's a 1-for-1, handled above)
                                if get_asset.vorp <= anchor.vorp * 1.15:
                                    continue
                                pr = _evaluate(opp, give, [get_asset])
                                if pr:
                                    found_for_partner.append(pr)
        # dedupe (same give/get set) and keep the best for this partner
        seen = set()
        uniq = []
        for pr in sorted(found_for_partner, key=lambda p: p.my_delta + p.their_delta,
                         reverse=True):
            key = (tuple(sorted(a.name for a in pr.give)),
                   tuple(sorted(a.name for a in pr.get)))
            if key in seen:
                continue
            seen.add(key)
            uniq.append(pr)
            if len(uniq) >= max_per_partner:
                break
        out.extend(uniq)

    out.sort(key=lambda p: p.my_delta + p.their_delta, reverse=True)
    return out


_TENDENCY_WANTS = {
    "RB-heavy": "RB", "hero-RB": "RB", "zero-RB": "WR",
    "WR-zealot": "WR", "QB-early": "QB", "TE-premium": "TE",
}


def _make_proposal(me: TeamProfile, opp: TeamProfile, give, get,
                   my_delta, their_delta, ratio) -> TradeProposal:
    g = get[0]
    # the "anchor" I send = the most valuable live piece in the package; the
    # pitch is built around what the OPPONENT actually values receiving.
    live_give = [a for a in give if not a.frozen]
    anchor = max(live_give, key=lambda a: a.vorp) if live_give else give[0]
    frozen_give = [a for a in give if a.frozen]
    is_package = len(give) > 1

    # does what I'm sending line up with how they draft? (makes the pitch real)
    flavor = ""
    for t in opp.tendencies:
        if _TENDENCY_WANTS.get(t) == anchor.position:
            flavor = (f" They draft {t} — {anchor.position} is exactly what they "
                      f"chase.")
            break
    their_hole = opp.weakest()[0] if opp.weakest() else anchor.position
    send_str = anchor.name + (f" (+ {frozen_give[0].name})" if frozen_give
                              else (f" + {give[1].name}" if is_package and not frozen_give
                                    else ""))
    pitch = (f"{opp.team_name} is thin at {their_hole} and deep enough elsewhere "
             f"to spare {g.name}. {anchor.name} ({anchor.position}) plugs their "
             f"hole; they trade from a strength to fix a weakness.{flavor}")

    # my side
    if is_package and frozen_give:
        my_reason = (f"Consolidation: you package {anchor.name} (surplus) with a "
                     f"dead roster spot — {frozen_give[0].name}, {frozen_give[0].frozen_reason or 'out for the year'} "
                     f"— to land {g.name}, a startable {g.position}. The injured "
                     f"body costs you nothing and makes the player counts even; "
                     f"you turn two bench spots into one starter at your biggest "
                     f"hole.")
    elif is_package:
        my_reason = (f"Consolidation: you give two bench pieces ({', '.join(a.name for a in give)}) "
                     f"for one starter, {g.name} ({g.position}) — fewer roster "
                     f"spots, more startable points at your hole.")
    elif anchor.frozen:
        my_reason = (f"You're trading a dead roster spot — {anchor.name} is done "
                     f"for the year ({anchor.frozen_reason}) — into a startable "
                     f"{g.position}. That's pure addition.")
    else:
        my_reason = (f"{g.name} steps into your {g.position} hole; {anchor.name} "
                     f"was surplus you weren't starting.")
    return TradeProposal(opp.team_name, give, get, my_delta, their_delta,
                         round(ratio, 2), pitch, my_reason)


# ---------------------------------------------------------------------------
# top-level convenience: build everything from an espn.team_rosters() payload
# ---------------------------------------------------------------------------
def build(rosters: dict, pool, cfg: E.LeagueConfig,
          season_ending: Optional[set[str]] = None,
          past_drafts: Optional[list] = None,
          allow_two_for_one: bool = True) -> dict:
    """One call from the UI.

    rosters       = espn.team_rosters()  (all teams)
    season_ending = names out for the year (e.g. {'Devon Achane'})
    past_drafts   = optional [[{slot,position,round}...]] for tendency learning
    allow_two_for_one = also propose 2-for-1 consolidation packages

    Returns {'me': TeamProfile, 'league': [TeamProfile...], 'trades': [TradeProposal...]}.
    """
    vidx = value_index(pool, cfg)
    teams = rosters.get("teams", []) or []

    # learn tendencies from draft history if provided (Tier 2)
    tend_by_slot: dict = {}
    if past_drafts:
        try:
            tend_by_slot = OPP.learn_from_history(past_drafts)
        except Exception:
            tend_by_slot = {}

    profiles: list[TeamProfile] = []
    me_profile: Optional[TeamProfile] = None
    for i, t in enumerate(teams, 1):
        tend = tend_by_slot.get(t.get("team_id")) or tend_by_slot.get(i)
        prof = profile_team(t, vidx, cfg, season_ending=season_ending,
                            tendencies=tend)
        profiles.append(prof)
        if prof.is_mine and me_profile is None:
            me_profile = prof

    if me_profile is None and profiles:
        me_profile = profiles[0]

    others = [p for p in profiles if p is not me_profile]
    trades = (find_trades(me_profile, others, cfg,
                          allow_two_for_one=allow_two_for_one)
              if me_profile else [])
    return {"me": me_profile, "league": profiles, "trades": trades}
