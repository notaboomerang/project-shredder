"""
Dark Horse — the last-pick lottery ticket, surfaced only when the time is right.

This scrapes the depths of the pool for the highest-CEILING sleeper: a player
almost nobody drafts, who has a real (if narrow) path to a league-winning
season — youth + elite athleticism, an ambiguous/vacated role one injury away
from volume, and a good landing spot (pace / pass-rate / dome). It is NOT a
best-available pick; it is a swing for the fences with your final bench slot.

THE GATE — it only fires when ALL of these are true, so it never distracts you
during the meaningful rounds:
  1. You are at (or within `late_window` of) your LAST pick.
  2. You have ALREADY rostered a K and a DST (the "assuming I picked K+DST"
     condition — you're truly at the end).
  3. The fiend is still on the board.
Otherwise it stays silent (returns None), exactly as asked: "only if the time
is right."
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import engine as E
import projections as P
import combine as CMB
import advanced_metrics as ADV
import venue as VEN


@dataclass
class DarkHorse:
    name: str
    position: str
    team: str
    adp: Optional[float]
    ceiling_score: float
    thesis: str            # the narrative — why this fiend, why now


def _ceiling_score(raw: P.RawPlayer, scoring: E.Scoring) -> tuple[float, list[str]]:
    """Score a player's league-winning UPSIDE (not floor). Rewards: deep ADP
    (nobody sees it coming), youth/rookie, elite RAS, ambiguous->volume role,
    good landing spot. Returns (score, reason_fragments)."""
    reasons: list[str] = []
    score = 0.0
    pos = raw.position
    if pos in ("K", "DST"):
        return -1e9, []            # never a dark horse

    adp = P.adp_for(raw, "half") or 200
    # obscurity: the deeper the ADP, the more "nobody sees it coming"
    obscurity = max(0.0, adp - 90) / 10.0     # 0 at ADP90, grows into the depths
    score += min(obscurity, 12)
    if adp >= 140:
        reasons.append(f"buried at ADP {adp:.0f} — nobody's watching")

    # youth / rookie ceiling
    if raw.rookie:
        score += 8; reasons.append("rookie with untapped ceiling")
    elif raw.age is not None and raw.age <= 24:
        score += 5; reasons.append(f"only {int(raw.age)} — ascending")

    # elite athletic profile (RAS) for rookie RB/WR/TE
    adelta, abadge = CMB.athletic_adjustment(raw.name, pos, raw.rookie)
    if abadge and ("ELITE" in abadge or "PLUS" in abadge):
        score += 6; reasons.append(abadge.lower())

    # opportunity / landing spot from advanced metrics
    mdelta, mbadges = ADV.metric_adjustments(raw.name, pos)
    for b in mbadges:
        if "PACE" in b.upper() or "PASS-HEAVY" in b.upper():
            score += 3; reasons.append(b.lower()); break

    # dome / passing environment (a hidden WR/TE boost)
    vrep = VEN.venue_report(raw.team)
    if vrep and vrep.indoor_share >= 0.55 and pos in ("WR", "TE", "QB"):
        score += 3; reasons.append("dome-friendly offense")

    # RB/WR are the positions where a vacated role explodes value
    if pos in ("RB", "WR"):
        score += 2

    return score, reasons


def find_dark_horse(pool: list[P.RawPlayer], cfg: E.LeagueConfig,
                    scoring_key: str = "half",
                    min_adp: float = 120.0) -> Optional[DarkHorse]:
    """Return the single highest-ceiling BURIED sleeper — a true dark horse.
    Hard filter: only players drafted DEEP (ADP >= min_adp) or effectively
    undrafted qualify, so it's never an early-round name. Among those, the
    highest ceiling score wins. None if nobody qualifies."""
    best: Optional[DarkHorse] = None
    best_score = -1e18
    for raw in pool:
        if raw.position in ("K", "DST"):
            continue
        adp = P.adp_for(raw, scoring_key)
        # the fiend must be buried: deep ADP or genuinely undrafted
        if adp is not None and adp < min_adp:
            continue
        sc, reasons = _ceiling_score(raw, cfg.scoring)
        if sc > best_score:
            best_score = sc
            adp_txt = f"{adp:.0f}" if adp else "undrafted"
            thesis = (f"THE FIEND: {raw.name} ({raw.position}, {raw.team}) — "
                      f"buried at ADP {adp_txt}, " + "; ".join(reasons[:3]) +
                      ". One broken tackle / one injury ahead from a "
                      "league-winning smash. This is your last-dart lottery "
                      "ticket — swing for the fences.")
            best = DarkHorse(name=raw.name, position=raw.position, team=raw.team,
                             adp=adp, ceiling_score=round(sc, 1), thesis=thesis)
    return best


def recommend_if_right(pool: list[P.RawPlayer], cfg: E.LeagueConfig,
                       roster_players: list[tuple[str, str]], drafted: set,
                       current_overall: int, scoring_key: str = "half",
                       late_window: int = 2) -> Optional[DarkHorse]:
    """THE GATED RECOMMENDATION. Returns the dark horse ONLY when the time is
    right; otherwise None (silent). Conditions:
      - within `late_window` picks of your LAST overall pick, AND
      - K and DST already on your roster, AND
      - the candidate is still available."""
    my_picks = cfg.my_overall_picks()
    if not my_picks:
        return None
    last_pick = my_picks[-1]
    # are we at/near the end?
    if current_overall < last_pick - late_window:
        return None
    # K + DST already rostered?
    have_pos = {pos for _, pos in roster_players}
    if not ({"K", "DST"} <= have_pos):
        return None
    # find the fiend among still-available players
    avail = [p for p in pool if p.name not in drafted]
    dh = find_dark_horse(avail, cfg, scoring_key)
    if dh is None or dh.name in drafted:
        return None
    return dh
