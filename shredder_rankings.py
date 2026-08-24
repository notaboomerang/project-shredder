"""
Shredder Rankings — our OWN 2026 board, built with all the logic we've layered.

Consensus ADP comes from blending every reliable public source we can fetch
(FantasyPros expert-consensus + FantasyFootballCalculator live mock ADP +
Sleeper), averaged per player (live_feed.consensus_adp). That's the "market."

Shredder Rank is our answer to the market: every player scored by the Edge
Engine COMPOSITE — projection -> VORP -> tiers, plus opportunity metrics
(target/snap share, O-line, pace), schedule/matchup softness, week-to-week
consistency, injury penalties, and the breakout/soul lean. We rank the whole
pool by that composite from a NEUTRAL empty roster (no positional-need skew) so
the number reflects raw player value, then show where Shredder disagrees with
consensus ADP (value = we like him more than the market; reach = less).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import engine as E
import edge_engine as X
import projections as P


@dataclass
class RankRow:
    shredder_rank: int
    name: str
    position: str
    team: str
    pos_rank: int
    tier: int
    composite: float
    vorp: float
    consensus_adp: Optional[float]
    adp_sources: int
    delta: Optional[float]        # consensus_adp - shredder_rank (+ = value, - = reach)
    verdict: str                  # VALUE / FAIR / REACH
    badges: list[str]


def build_rankings(pool: list[P.RawPlayer], cfg: E.LeagueConfig,
                   scoring_key: str = "half", top_n: int = 200) -> list[RankRow]:
    """Rank the whole pool by the Edge Engine composite (neutral empty roster),
    pair with consensus ADP, and flag value vs reach."""
    # neutral roster + no opponents so composite = pure player value, all logic on
    recs = X.recommend(pool, cfg, X.Roster(players=[]), set(),
                       current_overall=1, scoring_key=scoring_key,
                       top_n=top_n, opponents=None, prefer_floor=False)
    rows: list[RankRow] = []
    for i, r in enumerate(recs, start=1):
        adp = r.adp
        delta = round(adp - i, 1) if adp else None
        if delta is None:
            verdict = "—"
        elif delta >= 8:
            verdict = "VALUE"          # market ranks him ~8+ spots later than we do
        elif delta <= -8:
            verdict = "REACH"          # market ranks him well ahead of our number
        else:
            verdict = "FAIR"
        # source count rides on the RawPlayer (set by live_feed.fetch_pool)
        raw = next((p for p in pool if p.name == r.name), None)
        n_src = getattr(raw, "adp_sources", 1) if raw else 1
        rows.append(RankRow(
            shredder_rank=i, name=r.name, position=r.position, team=r.team,
            pos_rank=r.pos_rank, tier=r.tier, composite=round(r.composite, 1),
            vorp=round(r.vorp, 1), consensus_adp=adp, adp_sources=n_src,
            delta=delta, verdict=verdict, badges=r.badges[:4]))
    return rows
