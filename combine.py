"""
Combine / athletic-profile module (RAS-based).

Adds a rookie-only athletic signal on top of projections. For rookies there is
no NFL production to project from, so combine testing carries real predictive
weight — especially for RB/WR (size-adjusted speed & explosion). QB combine
numbers are noise for fantasy and are ignored.

Core input: RAS (Relative Athletic Score, 0-10) — a position-normalized
composite of 40-yд, vertical, broad, shuttle, 3-cone, and size. One clean
number per prospect. We also keep raw fields when available.

Output: athletic_adjustment(player) -> (score_delta, badge_or_None). It only
fires for rookies. High RAS boosts ceiling/composite; poor testing fades it.

DATA POSTURE: seeded 2026 rookie RAS below is a best-effort SNAPSHOT and is
OVERRIDABLE via data/combine.json. RAS is public per-prospect; swap in exact
values when confirmed rather than trusting these as frozen truth.
"""
from __future__ import annotations

import json
import os
from typing import Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_OVERRIDE = os.path.join(_DATA_DIR, "combine.json")

# Seeded 2026 rookie athletic profiles: name -> {"ras": 0-10, optional raw}.
# NOTE: illustrative seed values; confirm/replace via data/combine.json.
_SEED_RAS = {
    "Ashton Jeanty":     {"ras": 8.9, "forty": 4.44, "note": "elite size-adj burst"},
    "Omarion Hampton":   {"ras": 9.4, "forty": 4.46, "note": "rare size+speed workhorse"},
    "Colston Loveland":  {"ras": 8.1, "forty": 4.70, "note": "movable-chess-piece TE"},
    "Tyler Warren":      {"ras": 7.2, "forty": 4.80, "note": "big-bodied YAC TE"},
}

# only these positions get an athletic adjustment; QB combine = noise
_ATHLETIC_POS = {"RB", "WR", "TE"}


def load_ras() -> dict:
    if os.path.exists(_OVERRIDE):
        with open(_OVERRIDE, encoding="utf-8") as f:
            return json.load(f)
    return dict(_SEED_RAS)


def ras_for(name: str) -> Optional[float]:
    entry = load_ras().get(name)
    if not entry:
        return None
    r = entry.get("ras")
    return float(r) if r is not None else None


def athletic_adjustment(name: str, position: str, rookie: bool
                        ) -> tuple[float, Optional[str]]:
    """Return (composite_delta, badge). Rookie + RB/WR/TE only.

    RAS scale: 10 = 99th-pctile athlete, 5 = average, <4 = poor.
    We center at 5 and scale so an elite tester adds meaningful ceiling and a
    poor tester fades — but the magnitude is modest so it NUDGES, never
    overrides projection + landing spot."""
    if not rookie or position not in _ATHLETIC_POS:
        return 0.0, None
    r = ras_for(name)
    if r is None:
        return 0.0, None
    delta = (r - 5.0) * 2.0        # RAS 9 -> +8, RAS 3 -> -4
    if r >= 9.0:
        badge = f"ELITE ATHLETE (RAS {r})"
    elif r >= 7.5:
        badge = f"PLUS ATHLETE (RAS {r})"
    elif r < 4.5:
        badge = f"POOR TESTER (RAS {r})"
    else:
        badge = f"RAS {r}"
    return round(delta, 1), badge
