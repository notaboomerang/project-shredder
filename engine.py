"""
Draft config + scoring + VORP engine for the Fantasy Draft Assistant.

Everything the user asked to be DYNAMIC lives here as plain data:
  - scoring format (Std / 0.5 PPR / Full PPR) and custom point values
  - team count
  - the user's draft slot
  - the starting lineup (per-position slot counts + FLEX + SUPERFLEX)
  - bench size

The VORP baseline is COMPUTED from these, never hardcoded, so changing the
lineup or team count instantly re-ranks the board.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class Scoring:
    """Per-stat point values. Reception points is the format toggle."""
    reception: float = 0.5          # 0.0 = Standard, 0.5 = Half, 1.0 = Full PPR
    pass_yd: float = 0.04           # 1 pt / 25 yds
    pass_td: float = 4.0
    interception: float = -2.0
    rush_yd: float = 0.1            # 1 pt / 10 yds
    rush_td: float = 6.0
    rec_yd: float = 0.1
    rec_td: float = 6.0
    fumble_lost: float = -2.0
    # ---- Kicker ----
    fg_0_39: float = 3.0            # FG made, under 40 yds
    fg_40_49: float = 4.0
    fg_50: float = 5.0              # 50+ yds
    fg_miss: float = -1.0
    xp_made: float = 1.0
    xp_miss: float = -1.0
    # ---- D/ST ----
    dst_sack: float = 1.0
    dst_int: float = 2.0
    dst_fum_rec: float = 2.0
    dst_td: float = 6.0            # any defensive/return TD
    dst_safety: float = 2.0
    dst_block: float = 2.0
    dst_pa_0: float = 5.0          # points-allowed tiers (per game)
    dst_pa_1_6: float = 4.0
    dst_pa_7_13: float = 3.0
    dst_pa_14_17: float = 1.0
    dst_pa_18_27: float = 0.0
    dst_pa_28_34: float = -1.0
    dst_pa_35: float = -4.0

    @classmethod
    def preset(cls, name: str) -> "Scoring":
        name = name.lower().replace("-", "").replace(" ", "").replace("_", "")
        if name in ("std", "standard", "nonppr", "0ppr"):
            return cls(reception=0.0)
        if name in ("halfppr", "half", "05ppr", ".5ppr", "05"):
            return cls(reception=0.5)
        if name in ("ppr", "fullppr", "full", "1ppr"):
            return cls(reception=1.0)
        raise ValueError(f"unknown scoring preset: {name!r}")


def project_points(stats: dict, scoring: Scoring) -> float:
    """Convert a projection stat line into fantasy points for this scoring.
    Handles skill positions, Kicker (fg_*/xp_*), and D/ST (dst_*) stat lines."""
    g = stats.get
    pts = (
        g("rec", 0.0) * scoring.reception
        + g("pass_yd", 0.0) * scoring.pass_yd
        + g("pass_td", 0.0) * scoring.pass_td
        + g("int", 0.0) * scoring.interception
        + g("rush_yd", 0.0) * scoring.rush_yd
        + g("rush_td", 0.0) * scoring.rush_td
        + g("rec_yd", 0.0) * scoring.rec_yd
        + g("rec_td", 0.0) * scoring.rec_td
        + g("fumble_lost", 0.0) * scoring.fumble_lost
    )
    # Kicker
    pts += (g("fg_0_39", 0.0) * scoring.fg_0_39
            + g("fg_40_49", 0.0) * scoring.fg_40_49
            + g("fg_50", 0.0) * scoring.fg_50
            + g("fg_miss", 0.0) * scoring.fg_miss
            + g("xp_made", 0.0) * scoring.xp_made
            + g("xp_miss", 0.0) * scoring.xp_miss)
    # D/ST — turnovers, TDs, safeties, blocks + points-allowed (season totals)
    pts += (g("dst_sack", 0.0) * scoring.dst_sack
            + g("dst_int", 0.0) * scoring.dst_int
            + g("dst_fum_rec", 0.0) * scoring.dst_fum_rec
            + g("dst_td", 0.0) * scoring.dst_td
            + g("dst_safety", 0.0) * scoring.dst_safety
            + g("dst_block", 0.0) * scoring.dst_block
            + g("dst_pa_pts", 0.0))    # pre-computed PA points (tier * games)
    return pts


# ---------------------------------------------------------------------------
# League / lineup config
# ---------------------------------------------------------------------------

# Which real positions can fill each flexible slot type.
FLEX_ELIGIBLE = {
    "FLEX": ("RB", "WR", "TE"),
    "WRRB_FLEX": ("RB", "WR"),
    "REC_FLEX": ("WR", "TE"),
    "SUPERFLEX": ("QB", "RB", "WR", "TE"),
    "OP": ("QB", "RB", "WR", "TE"),  # ESPN "OP" = offensive player = superflex
}


@dataclass
class LeagueConfig:
    teams: int = 12
    draft_slot: int = 11            # 1-indexed pick position
    rounds: int = 16
    scoring: Scoring = field(default_factory=lambda: Scoring.preset("half"))

    # starting-lineup slot counts
    starters: dict = field(default_factory=lambda: {
        "QB": 1, "RB": 2, "WR": 2, "TE": 1,
        "FLEX": 1, "DST": 1, "K": 1,
    })
    bench: int = 7

    # ---- Monday's ESPN league, read straight off the roster screenshot ----
    @classmethod
    def monday(cls) -> "LeagueConfig":
        return cls(
            teams=12, draft_slot=11, rounds=16,
            scoring=Scoring.preset("half"),
            starters={"QB": 1, "RB": 2, "WR": 2, "TE": 1,
                      "FLEX": 1, "DST": 1, "K": 1},
            bench=7,
        )

    # ---- snake-draft pick math for the user's slot ----
    def my_overall_picks(self) -> list[int]:
        """Overall pick numbers for this slot across all rounds (snake)."""
        picks = []
        for rnd in range(1, self.rounds + 1):
            if rnd % 2 == 1:                      # odd rounds go top->bottom
                pick = (rnd - 1) * self.teams + self.draft_slot
            else:                                 # even rounds snake back
                pick = rnd * self.teams - (self.draft_slot - 1)
            picks.append(pick)
        return picks

    def gaps_between_my_picks(self) -> list[int]:
        picks = self.my_overall_picks()
        return [picks[i + 1] - picks[i] for i in range(len(picks) - 1)]


# ---------------------------------------------------------------------------
# VORP baseline + valuation
# ---------------------------------------------------------------------------

def replacement_ranks(cfg: LeagueConfig) -> dict[str, int]:
    """
    Per-position DEDICATED-starter replacement rank = teams × starters[pos].

    Baseline rank R means "the Rth-best player at this position is the
    replacement", i.e. VORP is measured against projected points of pos-rank R.

    NOTE: FLEX is intentionally NOT distributed here anymore. Flex demand is
    handled as a SINGLE combined RB/WR/TE pool in compute_vorp (see
    flex_replacement_points), so a flex-worthy player at ANY eligible position
    is valued off one shared flex line instead of a fractional per-position
    sprinkle. Dedicated-starter ranks stay per-position.
    """
    teams = cfg.teams
    s = cfg.starters
    demand = {pos: teams * s.get(pos, 0) for pos in ("QB", "RB", "WR", "TE", "K", "DST")}
    return {pos: max(1, math.ceil(d)) for pos, d in demand.items() if d > 0}


def flex_replacement_points(players: list["PlayerValue"], cfg: LeagueConfig
                            ) -> dict[str, float]:
    """Replacement POINTS for each flex-type slot, from a SINGLE combined pool.

    For each flex slot (FLEX, SUPERFLEX, ...), pool every eligible player across
    its positions, drop the players already claimed by their DEDICATED starter
    ranks, then the next `teams × count` fill the flex slots. The first player
    BEYOND that is the flex replacement — its projected points is the single
    line a flex-worthy player at any eligible position must clear.

    Returns {slot_name: replacement_points}. Empty when no flex slots exist.
    """
    teams = cfg.teams
    s = cfg.starters
    out: dict[str, float] = {}

    # dedicated starters already spoken for at each real position
    dedicated = {pos: teams * s.get(pos, 0) for pos in ("QB", "RB", "WR", "TE", "K", "DST")}

    # players by position, sorted best -> worst by projection
    by_pos: dict[str, list[float]] = {}
    for p in players:
        by_pos.setdefault(p.position, []).append(p.proj_points)
    for pos in by_pos:
        by_pos[pos].sort(reverse=True)

    for slot, count in s.items():
        if slot not in FLEX_ELIGIBLE or not count:
            continue
        elig = FLEX_ELIGIBLE[slot]
        # remaining flex-eligible players AFTER each position's dedicated starters
        pool: list[float] = []
        for pos in elig:
            pts = by_pos.get(pos, [])
            pool.extend(pts[dedicated.get(pos, 0):])
        pool.sort(reverse=True)
        if not pool:
            # Pool smaller than dedicated demand (e.g. a tiny/late board): fall
            # back to the combined-flex line over the FULL eligible pool at the
            # equivalent absolute depth, so we never return a bogus 0.0 that
            # would wipe out VORP. None => caller keeps the dedicated line.
            full = sorted((pt for pos in elig for pt in by_pos.get(pos, [])),
                          reverse=True)
            if not full:
                continue
            ded_total = sum(dedicated.get(pos, 0) for pos in elig)
            idx = min(ded_total + teams * count, len(full) - 1)
            out[slot] = full[idx]
            continue
        flex_slots = teams * count
        # the flex starters are pool[0:flex_slots]; the replacement is the very
        # next player (index flex_slots), clamped to the pool.
        idx = min(flex_slots, len(pool) - 1)
        out[slot] = pool[idx]
    return out


@dataclass
class PlayerValue:
    player_id: str
    name: str
    position: str
    team: str
    proj_points: float
    vorp: float = 0.0
    pos_rank: int = 0
    tier: int = 0
    adp: Optional[float] = None
    value_vs_adp: Optional[float] = None   # +ve = value (ranks ahead of ADP)
    # TE (and RB/WR) are evaluated TWICE: once at their own position, once in the
    # shared FLEX pool vs all RB/WR/TE. vorp = the better of the two (easier line
    # to clear). These expose each component for display / debugging.
    vorp_pos: float = 0.0                  # value vs own-position replacement
    vorp_flex: Optional[float] = None      # value vs the combined FLEX line (None if not flex-eligible)


def compute_vorp(players: list[PlayerValue], cfg: LeagueConfig) -> list[PlayerValue]:
    """Rank within position, set replacement baseline, compute VORP + tiers.

    FLEX is a SINGLE combined RB/WR/TE pool: a flex-eligible player is valued
    off whichever line is EASIER to clear — its own dedicated-position
    replacement OR the shared flex replacement (min points) — because clearing
    either earns a starting spot. This makes, e.g., a top-tier TE in a 1-TE
    league still valued for the flex it can win, while a stockpiled 2nd TE that
    beats neither line correctly grades as bench.
    """
    baselines = replacement_ranks(cfg)
    flex_pts = flex_replacement_points(players, cfg)

    # the single flex line that applies to RB/WR/TE (standard FLEX). If multiple
    # flex-type slots exist, a position uses the LOWEST flex line it's eligible
    # for (easiest bar to clear).
    def _flex_line_for(pos: str) -> Optional[float]:
        lines = [pv for slot, pv in flex_pts.items()
                 if pos in FLEX_ELIGIBLE.get(slot, ())]
        return min(lines) if lines else None

    by_pos: dict[str, list[PlayerValue]] = {}
    for p in players:
        by_pos.setdefault(p.position, []).append(p)

    # STREAMABLE / single-start positions: you start only ONE and a late-tier
    # option scores nearly as much, so their value-over-replacement must be
    # SMALL — otherwise a high-raw-scoring QB (Josh Allen) or an elite DST floats
    # to the top of the board and looks like a round-1 pick, which it isn't in a
    # 1-QB league. We set their replacement to a SHALLOW rank (near the best),
    # i.e. a HIGH points floor, which collapses elite-QB/DST/K VORP so RB/WR/TE
    # win the early rounds. In SUPERFLEX/OP leagues QB is a real weekly need, so
    # we DON'T shallow-cap QB there. Ranks mirror the strategy simulator.
    # (TE is NOT shallow-capped — it rides the combined FLEX line above, which is
    # what keeps a lone elite TE valued while a 2nd TE grades as bench.)
    _superflex = any(sl in ("SUPERFLEX", "OP") and c for sl, c in cfg.starters.items())
    shallow = {"DST": 4, "K": 4}
    if not _superflex:
        shallow["QB"] = 8   # ~QB8's points as the replacement in a 1-QB league

    for pos, plist in by_pos.items():
        plist.sort(key=lambda x: x.proj_points, reverse=True)
        base_rank = baselines.get(pos, len(plist))
        idx = min(base_rank, len(plist)) - 1
        dedicated_pts = plist[idx].proj_points if plist else 0.0
        # Streamable single-start positions (QB in 1-QB, DST, K): use a SHALLOW
        # replacement (near the top of the pool) so elite raw scorers don't
        # dominate the board — their edge over a streamed option is tiny.
        if pos in shallow and plist:
            sidx = min(shallow[pos], len(plist)) - 1
            dedicated_pts = max(dedicated_pts, plist[sidx].proj_points)

        # DUAL EVALUATION for flex-eligible players: value them (1) at their own
        # position and (2) in the combined FLEX pool vs all active RB/WR/TE. A
        # player earns a starting spot by clearing EITHER line, so their true
        # value-over-replacement is measured against the EASIER (lower) line.
        # This keeps a scarce elite TE valuable on its own positional line while
        # also crediting the flex it can win — and RB/WR get the same treatment.
        fl = _flex_line_for(pos)
        for i, p in enumerate(plist, start=1):
            p.pos_rank = i
            p.vorp_pos = round(p.proj_points - dedicated_pts, 1)
            if fl is not None:
                p.vorp_flex = round(p.proj_points - fl, 1)
                # best of the two lines = value against the easier bar to start
                p.vorp = max(p.vorp_pos, p.vorp_flex)
            else:
                p.vorp_flex = None
                p.vorp = p.vorp_pos

    players.sort(key=lambda x: x.vorp, reverse=True)
    _assign_tiers(players)
    return players


def _assign_tiers(players: list[PlayerValue], gap: float = 12.0) -> None:
    """Group into tiers per position by VORP drop >= `gap` points (a cliff)."""
    by_pos: dict[str, list[PlayerValue]] = {}
    for p in players:
        by_pos.setdefault(p.position, []).append(p)
    for plist in by_pos.values():
        plist.sort(key=lambda x: x.vorp, reverse=True)
        tier = 1
        for i, p in enumerate(plist):
            if i > 0 and (plist[i - 1].vorp - p.vorp) >= gap:
                tier += 1
            p.tier = tier
