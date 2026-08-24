"""
Player projections + ADP loader.

Live path: pull consensus projections/ADP (FantasyPros pages / CSV). If the
network is down or a fetch fails, fall back to a SEEDED 2026 pool bundled in
data/players_seed.json so the app ALWAYS ranks players on Monday night.

Projections are stat lines (rec, rec_yd, rec_td, rush_yd, rush_td, pass_yd,
pass_td, int, fumble_lost) so engine.project_points() can score them for ANY
scoring format. ADP is stored per-format where available.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_SEED = os.path.join(_DATA_DIR, "players_seed.json")


@dataclass
class RawPlayer:
    name: str
    position: str
    team: str
    stats: dict                      # projection stat line
    adp: dict = field(default_factory=dict)   # {"ppr":.., "half":.., "std":..}
    bye: Optional[int] = None
    age: Optional[float] = None
    years_exp: Optional[int] = None
    rookie: bool = False
    espn_id: Optional[int] = None
    adp_sources: int = 1             # how many ADP sources agreed (consensus depth)


def load_players(prefer_live: bool = True) -> list[RawPlayer]:
    """Return the player pool. Tries live, falls back to seed. Never raises."""
    if prefer_live and requests is not None:
        try:
            live = _fetch_live()
            if live:
                return live
        except Exception:
            pass
    return _load_seed()


def _load_seed() -> list[RawPlayer]:
    if not os.path.exists(_SEED):
        return []
    with open(_SEED, encoding="utf-8") as f:
        raw = json.load(f)
    return [RawPlayer(**p) for p in raw]


def _fetch_live() -> list[RawPlayer]:
    """Live FantasyPros pull via live_feed.fetch_pool(). Returns [] on any
    failure so load_players() cleanly falls back to the seed. Enriches with
    bye/age/rookie/RAS-relevant flags from the seed when the name matches."""
    try:
        import live_feed
    except Exception:
        return []
    rows = live_feed.fetch_pool()
    if not rows:
        return []
    # seed lookup for enrichment (bye/age/rookie not on FantasyPros proj table)
    seed_by_name = {p.name: p for p in _load_seed()}
    out: list[RawPlayer] = []
    for r in rows:
        s = seed_by_name.get(r["name"])
        out.append(RawPlayer(
            name=r["name"], position=r["position"], team=r.get("team", ""),
            stats=r.get("stats", {}), adp=r.get("adp", {}),
            bye=(r.get("bye") if r.get("bye") else (s.bye if s else None)),
            age=(s.age if s else None),
            years_exp=(s.years_exp if s else None),
            rookie=(s.rookie if s else False),
            espn_id=(s.espn_id if s else None),
            adp_sources=r.get("adp_sources", 1),
        ))
    return out


def adp_for(player: RawPlayer, scoring_key: str) -> Optional[float]:
    """scoring_key in {'ppr','half','std'}. Falls back across formats."""
    for k in (scoring_key, "half", "ppr", "std"):
        if k in player.adp and player.adp[k]:
            return float(player.adp[k])
    return None
