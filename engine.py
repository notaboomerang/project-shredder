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
    Compute the replacement-level rank per position = the count of that
    position that will be rostered as STARTERS across the whole league,
    with flex/superflex demand distributed to the positions that fill them.

    Baseline rank R means "the Rth-best player at this position is the
    replacement", i.e. VORP is measured against projected points of pos-rank R.
    """
    teams = cfg.teams
    s = cfg.starters

    # dedicated starters at each real position
    demand = {pos: teams * s.get(pos, 0) for pos in ("QB", "RB", "WR", "TE", "K", "DST")}

    # distribute each flex-type slot's league-wide demand across eligible positions.
    # Split by a rough real-world usage weight (RB/WR carry most flex snaps).
    weights = {"QB": 0.15, "RB": 0.45, "WR": 0.45, "TE": 0.20}
    for slot, count in s.items():
        if slot in FLEX_ELIGIBLE and count:
            elig = FLEX_ELIGIBLE[slot]
            wsum = sum(weights.get(p, 0) for p in elig)
            total = teams * count
            for p in elig:
                demand[p] = demand.get(p, 0) + total * (weights.get(p, 0) / wsum)

    # replacement rank = ceil of starter demand (first NON-starter is the baseline)
    return {pos: max(1, math.ceil(d)) for pos, d in demand.items() if d > 0}


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


def compute_vorp(players: list[PlayerValue], cfg: LeagueConfig) -> list[PlayerValue]:
    """Rank within position, set replacement baseline, compute VORP + tiers."""
    baselines = replacement_ranks(cfg)

    by_pos: dict[str, list[PlayerValue]] = {}
    for p in players:
        by_pos.setdefault(p.position, []).append(p)

    for pos, plist in by_pos.items():
        plist.sort(key=lambda x: x.proj_points, reverse=True)
        base_rank = baselines.get(pos, len(plist))
        # replacement points = projection of the player AT the baseline rank
        idx = min(base_rank, len(plist)) - 1
        base_pts = plist[idx].proj_points if plist else 0.0
        for i, p in enumerate(plist, start=1):
            p.pos_rank = i
            p.vorp = round(p.proj_points - base_pts, 1)

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
