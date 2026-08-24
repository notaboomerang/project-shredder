"""
Opponent modeling — predict who actually survives to YOUR next pick.

The naive survival model (edge_engine.survival_probability) assumes everyone
drafts to league-average ADP. This module replaces that with an OPPONENT-AWARE
model: it accounts for how the SPECIFIC managers picking between now and your
next turn tend to draft.

Three tiers, degrading gracefully:
  Tier 1  Tendency profiles you set once (or defaults): QB-early, RB-heavy,
          WR-zealot, TE-premium, rookie-averse, homer(team), ADP-robot.
  Tier 2  Learn tendencies from PAST DRAFTS (Sleeper/ESPN history) — hook here.
  Tier 3  Live adaptation: watch each opponent's real picks this draft and
          nudge their profile (e.g. RB-RB-RB => RB run is on).

Output: opponent_aware_survival(player, ...) in 0..1 — probability the player
is still on the board when you next pick.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# tendency -> how it multiplies a candidate's take-likelihood, by position.
# >1 means this manager is MORE likely than ADP to grab that position.
TENDENCY_POS_BIAS = {
    "QB-early":      {"QB": 2.2},
    "RB-heavy":      {"RB": 1.8, "WR": 0.7},
    "WR-zealot":     {"WR": 1.8, "RB": 0.7},
    "TE-premium":    {"TE": 2.5},
    "ADP-robot":     {},                       # pure ADP, no bias
    "zero-RB":       {"RB": 0.4, "WR": 1.5, "TE": 1.2},
    "hero-RB":       {"RB": 1.3},
}


@dataclass
class OpponentProfile:
    slot: int                                  # draft slot (1-indexed)
    name: str = ""
    tendencies: list[str] = field(default_factory=lambda: ["ADP-robot"])
    rookie_averse: bool = False
    favorite_team: Optional[str] = None        # "homer" bias toward this team
    # live-learned position counts this draft (Tier 3)
    live_pos_counts: dict = field(default_factory=dict)

    def pos_multiplier(self, position: str, team: Optional[str],
                       rookie: bool) -> float:
        mult = 1.0
        for t in self.tendencies:
            mult *= TENDENCY_POS_BIAS.get(t, {}).get(position, 1.0)
        if self.rookie_averse and rookie:
            mult *= 0.35
        if self.favorite_team and team == self.favorite_team:
            mult *= 2.0
        # Tier 3 live nudge: if they've already loaded a position, slightly
        # less likely to keep hammering it (roster balance) unless RB-heavy.
        have = self.live_pos_counts.get(position, 0)
        if have >= 2 and "RB-heavy" not in self.tendencies:
            mult *= 0.8
        return mult


@dataclass
class LeagueOpponents:
    """All manager profiles keyed by draft slot. You (the user) are excluded
    from 'taking' players — your slot is skipped in the between-picks window."""
    profiles: dict[int, OpponentProfile] = field(default_factory=dict)
    my_slot: int = 1
    teams: int = 12

    @classmethod
    def default(cls, teams: int, my_slot: int) -> "LeagueOpponents":
        profiles = {s: OpponentProfile(slot=s, tendencies=["ADP-robot"])
                    for s in range(1, teams + 1)}
        return cls(profiles=profiles, my_slot=my_slot, teams=teams)

    def slots_between(self, current_overall: int, my_next_overall: int) -> list[int]:
        """Which draft slots pick in (current_overall .. my_next_overall) — the
        opponents who could snap up a player before I pick again (snake-aware)."""
        slots = []
        for ov in range(current_overall, my_next_overall):
            slots.append(_snake_slot(ov, self.teams))
        return [s for s in slots if s != self.my_slot]

    def note_pick(self, slot: int, position: str) -> None:
        """Tier 3: record a real pick to adapt the profile live."""
        p = self.profiles.get(slot)
        if p:
            p.live_pos_counts[position] = p.live_pos_counts.get(position, 0) + 1


def _snake_slot(overall: int, teams: int) -> int:
    rnd = (overall - 1) // teams + 1
    idx = (overall - 1) % teams
    return idx + 1 if rnd % 2 == 1 else teams - idx


def opponent_aware_survival(adp: Optional[float], position: str,
                            team: Optional[str], rookie: bool,
                            current_overall: int, my_next_overall: int,
                            opponents: LeagueOpponents) -> Optional[float]:
    """Probability the player survives to my next pick, given the SPECIFIC
    opponents picking in between and their tendencies.

    Model: each intervening opponent independently 'considers' the player with a
    base take-probability from ADP proximity, scaled by their positional bias.
    Survival = product over opponents of (1 - take_prob_i)."""
    if adp is None:
        return None
    slots = opponents.slots_between(current_overall, my_next_overall)
    if not slots:
        return 1.0

    survive = 1.0
    for i, slot in enumerate(slots):
        this_overall = current_overall + i
        # base per-pick take prob: high if this pick is at/after his ADP
        delta = this_overall - adp
        base = 1.0 / (1.0 + math.exp(-delta / 3.0))   # logistic on ADP proximity
        # a single manager only fills ONE slot, so cap base contribution
        base *= 0.6
        prof = opponents.profiles.get(slot)
        mult = prof.pos_multiplier(position, team, rookie) if prof else 1.0
        take = max(0.0, min(0.95, base * mult))
        survive *= (1.0 - take)
    return round(max(0.0, min(1.0, survive)), 2)


# ---- Tier 2 hook: learn from draft history --------------------------------
def learn_from_history(past_drafts: list[list[dict]]) -> dict[int, list[str]]:
    """Given past drafts (each a list of pick dicts with 'slot' and 'position'),
    infer a tendency label per slot. Hook for Sleeper/ESPN history pulls.

    Heuristic: if a slot drafts a position notably earlier/more than average,
    tag the matching tendency. Returns {slot: [tendencies]}."""
    from collections import defaultdict
    early_pos = defaultdict(lambda: defaultdict(int))  # slot -> pos -> early count
    for draft in past_drafts:
        for pick in draft:
            slot = pick.get("slot")
            pos = pick.get("position")
            rnd = pick.get("round", 99)
            if slot is None or pos is None:
                continue
            if rnd <= 4:                       # "early" = first 4 rounds
                early_pos[slot][pos] += 1
    out: dict[int, list[str]] = {}
    for slot, counts in early_pos.items():
        tags = []
        if counts.get("QB", 0) >= 2:
            tags.append("QB-early")
        if counts.get("TE", 0) >= 2:
            tags.append("TE-premium")
        if counts.get("RB", 0) >= 3:
            tags.append("RB-heavy")
        if counts.get("WR", 0) >= 3:
            tags.append("WR-zealot")
        out[slot] = tags or ["ADP-robot"]
    return out
