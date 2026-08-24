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
                     prof, loyalty_mult: float = 1.0) -> float:
    """How much this manager wants this player at this pick.

    PRIORITY (matches Strategy Sim): ESPN rankings (ADP) are the BASE board —
    NOT our VORP — then DNA (tendency) is the primary multiplier, then loyalty
    ('their guy', via loyalty_mult) is the override that jumps a repeat pick up.
    VORP is only a negligible tiebreaker so our engine never steers opponents."""
    a = adp if adp is not None else 400.0
    base = 1000.0 / (a + 8.0)               # better (lower) ADP = higher want
    if adp is not None and overall < adp - 12:
        base *= 0.35                         # too early unless loyal
    s = base
    if prof:                                 # DNA: primary lean multiplier
        s *= prof.pos_multiplier(pv.position, None, False)
    if pv.position in ("K", "DST"):
        s *= 0.03 if overall < 130 else 1.5  # streamed positions go late
    s *= loyalty_mult                        # loyalty override
    s += pv.vorp * 0.001                     # VORP: negligible tiebreaker only
    return s


def predict_board(pool, cfg: E.LeagueConfig, drafted: set,
                  current_overall: int, opponents=None,
                  scoring_key: str = "half", horizon: int = 24,
                  loyalty_by_slot: Optional[dict] = None) -> list[Prediction]:
    """Predict the next `horizon` picks. Greedy: at each pick, assign the
    manager's most likely target and remove it (so downstream predictions
    account for it), like a most-likely-path rollout.

    `loyalty_by_slot`: {slot: {normalized_player_name: times_drafted}} — a
    manager's repeat-drafted 'guys' get a named-player boost, so the crystal
    ball predicts the actual player they keep taking, not just the position."""
    import re

    def _norm(s):
        s = (s or "").lower().strip()
        s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
        s = re.sub(r"[^a-z0-9 ]", "", s)
        return re.sub(r"\s+", " ", s).strip()

    loyalty_by_slot = loyalty_by_slot or {}
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
        loyal = loyalty_by_slot.get(slot, {})    # {norm_name: count}
        scored = []
        for nm, pv in avail.items():
            adp = P.adp_for(raw_by_name[nm], scoring_key)
            # loyalty: a manager who drafts 'their guy' every year will REACH
            # for him. 2x drafted → strong pull, 3x+ → they take him on sight.
            lc = loyal.get(_norm(nm), 0)
            lmult = 1.0 + (2.5 * lc) if lc >= 2 else 1.0
            scored.append((nm, pv.position,
                           _score_candidate(pv, adp, ov, prof, lmult), lc))
        scored.sort(key=lambda x: x[2], reverse=True)
        top_raw = scored[:3]
        tot = sum(s for _, _, s, _ in top_raw) or 1.0
        top = [(nm, pos, round(s / tot, 2)) for nm, pos, s, _ in top_raw]
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
