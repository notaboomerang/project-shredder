"""
Draft Prophecy — predict who each opponent takes at every upcoming pick.

For every pick between now and (a horizon), it takes the slot's manager, scores
the available board through THEIR tendency profile + ADP proximity + VORP, and
names their most likely target with a confidence. Then it flags SNIPES: players
a specific opponent covets that YOU could grab first at one of your picks —
"take X now to deny slot 7, who wants him at pick 20."

Reuses opponents.py (tendencies) + engine VORP + ADP. Pure functions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import engine as E
import projections as P
import opponents as OPP


def _snake_slot(overall: int, teams: int) -> int:
    rnd = (overall - 1) // teams + 1
    idx = (overall - 1) % teams
    return idx + 1 if rnd % 2 == 1 else teams - idx


@dataclass
class Prediction:
    overall: int
    slot: int
    is_me: bool
    top: list[tuple[str, str, float]]   # [(name, position, confidence 0-1)]


@dataclass
class Snipe:
    player: str
    position: str
    coveted_by_slot: int
    their_pick_overall: int
    your_pick_overall: int              # the pick where you could take him first
    confidence: float


def _score_candidate(pv: E.PlayerValue, adp: Optional[float], overall: int,
                     prof) -> float:
    """How much this manager wants this player at this pick."""
    import math
    # ADP proximity: peaks when the pick number is near/after the player's ADP
    if adp is not None:
        prox = 1.0 / (1.0 + math.exp(-(overall - adp) / 4.0))
    else:
        prox = 0.15
    base = max(0.05, pv.vorp + 60) * (0.5 + prox)
    if prof:
        base *= prof.pos_multiplier(pv.position, None, False)
    if pv.position in ("K", "DST"):
        base *= 0.03 if overall < 130 else 1.5   # only late
    return base


def predict_board(pool, cfg: E.LeagueConfig, drafted: set,
                  current_overall: int, opponents=None,
                  scoring_key: str = "half", horizon: int = 24) -> list[Prediction]:
    """Predict the next `horizon` picks. Greedy: at each pick, assign the
    manager's most likely target and remove it (so downstream predictions
    account for it), like a most-likely-path rollout."""
    # available pool scored + VORP'd
    pvs = []
    raw_by_name = {}
    for raw in pool:
        if raw.name in drafted:
            continue
        pts = E.project_points(raw.stats, cfg.scoring)
        pvs.append(E.PlayerValue(raw.name, raw.name, raw.position, raw.team, pts))
        raw_by_name[raw.name] = raw
    E.compute_vorp(pvs, cfg)
    avail = {pv.name: pv for pv in pvs}

    preds: list[Prediction] = []
    teams = cfg.teams
    for ov in range(current_overall, current_overall + horizon):
        slot = _snake_slot(ov, teams)
        prof = opponents.profiles.get(slot) if opponents else None
        scored = []
        for nm, pv in avail.items():
            adp = P.adp_for(raw_by_name[nm], scoring_key)
            scored.append((nm, pv.position,
                           _score_candidate(pv, adp, ov, prof)))
        scored.sort(key=lambda x: x[2], reverse=True)
        top_raw = scored[:3]
        tot = sum(s for _, _, s in top_raw) or 1.0
        top = [(nm, pos, round(s / tot, 2)) for nm, pos, s in top_raw]
        preds.append(Prediction(overall=ov, slot=slot,
                                 is_me=(slot == cfg.draft_slot), top=top))
        # consume the most-likely target on the greedy path
        if top:
            avail.pop(top[0][0], None)
    return preds


def find_snipes(preds: list[Prediction], cfg: E.LeagueConfig,
                min_conf: float = 0.4) -> list[Snipe]:
    """A snipe = a player an OPPONENT is predicted to take, whom YOU have an
    earlier pick to grab first. 'Take him now to deny them.'"""
    my_future = [p.overall for p in preds if p.is_me]
    snipes: list[Snipe] = []
    for pred in preds:
        if pred.is_me or not pred.top:
            continue
        name, pos, conf = pred.top[0]
        if conf < min_conf:
            continue
        # do I have a pick BEFORE their pick where I could take him?
        earlier = [o for o in my_future if o < pred.overall]
        if earlier:
            snipes.append(Snipe(player=name, position=pos,
                                coveted_by_slot=pred.slot,
                                their_pick_overall=pred.overall,
                                your_pick_overall=max(earlier),
                                confidence=conf))
    # dedup by player, keep highest confidence
    best: dict[str, Snipe] = {}
    for s in snipes:
        if s.player not in best or s.confidence > best[s.player].confidence:
            best[s.player] = s
    return sorted(best.values(), key=lambda s: s.confidence, reverse=True)
