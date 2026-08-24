"""
Copilot — warfare, prediction cues, and vibes layered on live draft state.

All pure functions over (recent picks, your roster, board). No new deps.
  • run_detector          — "🔥 RB run: 3 of last 4 picks"
  • nemesis               — the opponent roster most threatening to yours
  • tilt                  — flag a manager likely rattled (just got sniped)
  • team_name             — names your squad from its composition
  • trash_talk            — a cocky one-liner for a pick (paste in league chat)
  • villain_line          — heist-movie narration of your draft arc
  • chaos_pick            — highest-variance ceiling swing (punt-the-season mode)
"""
from __future__ import annotations

import random
from collections import Counter
from typing import Optional


# ---- run detector ---------------------------------------------------------
def run_detector(recent_positions: list[str], window: int = 5) -> Optional[str]:
    """recent_positions = positions of the last few picks (most-recent last)."""
    w = recent_positions[-window:]
    if len(w) < 3:
        return None
    c = Counter(w)
    pos, cnt = c.most_common(1)[0]
    if pos in ("RB", "WR", "TE", "QB") and cnt >= 3:
        return f"🔥 {pos} RUN — {cnt} of the last {len(w)} picks are {pos}. " \
               f"The position is emptying; grab yours NOW or pivot."
    return None


# ---- nemesis --------------------------------------------------------------
def nemesis(team_rosters: dict[int, list[str]], my_slot: int,
            pos_of: dict) -> Optional[str]:
    """team_rosters: slot -> [player names]. Finds the opponent whose roster
    shape most mirrors/threatens yours (same positional strengths)."""
    mine = Counter(pos_of.get(n, "?") for n in team_rosters.get(my_slot, []))
    if not mine:
        return None
    best_slot, best_overlap = None, -1
    for slot, names in team_rosters.items():
        if slot == my_slot or not names:
            continue
        theirs = Counter(pos_of.get(n, "?") for n in names)
        overlap = sum(min(mine[p], theirs[p]) for p in mine)
        if overlap > best_overlap:
            best_overlap, best_slot = overlap, slot
    if best_slot is not None and best_overlap >= 2:
        return (f"🎯 Nemesis: manager in slot {best_slot} is building the same "
                f"shape as you ({best_overlap} shared position depth). Deny the "
                f"players they need most.")
    return None


# ---- tilt -----------------------------------------------------------------
def tilt(last_pick_slot: Optional[int], last_pick_was_reach: bool) -> Optional[str]:
    if last_pick_slot and last_pick_was_reach:
        return (f"😤 Slot {last_pick_slot} just reached — likely rattled/on tilt. "
                f"Expect another panic pick; the value they skip can fall to you.")
    return None


# ---- team namer -----------------------------------------------------------
_NAMES = {
    "RB": "Ground & Pound", "WR": "The Air Raid", "TE": "Seam Kings",
    "QB": "Gunslingers", "BALANCED": "The Balanced Attack",
}


def team_name(roster_positions: list[str]) -> str:
    if not roster_positions:
        return "The Blank Slate"
    c = Counter(roster_positions)
    skill = {p: c.get(p, 0) for p in ("RB", "WR", "TE", "QB")}
    top = max(skill, key=skill.get)
    if skill[top] >= 4 and skill[top] - sorted(skill.values())[-2] >= 2:
        return _NAMES.get(top, "The Squad")
    return _NAMES["BALANCED"]


# ---- trash talk -----------------------------------------------------------
_TRASH = [
    "Just drafted {p}. You can all play for 2nd now.",
    "{p} to my squad. Screenshot this for the group chat hall of fame.",
    "Took {p}. The value was criminal — someone call the cops.",
    "{p}. I'd apologize to the league but I'm not sorry.",
    "Locked {p}. The championship trophy is already being engraved.",
]


def trash_talk(player: str) -> str:
    return random.choice(_TRASH).format(p=player)


# ---- villain narration ----------------------------------------------------
def villain_line(round_no: int, my_last: Optional[str]) -> str:
    if round_no <= 2:
        return "The heist begins. You case the board while the room panics."
    if round_no <= 5 and my_last:
        return f"You let them burn their picks, then struck — {my_last} is yours."
    if round_no <= 9:
        return "The trap is set. Your roster hums while rivals scramble for scraps."
    return "Endgame. You reach into the depths for the dagger that ends them."


# ---- chaos mode -----------------------------------------------------------
def chaos_pick(recs: list) -> Optional[object]:
    """From the current recommendations, return the highest-CEILING swing
    (youngest / rookie / biggest value-vs-ADP) — the punt-the-season lottery.
    recs = list of Recommendation objects (have .badges, .value_vs_adp, .name)."""
    if not recs:
        return None
    def ceiling(r):
        sc = (r.value_vs_adp or 0)
        badges = " ".join(r.badges).upper()
        if "ROOKIE" in badges or "BREAKOUT" in badges:
            sc += 20
        if "ELITE ATHLETE" in badges or "PLUS ATHLETE" in badges:
            sc += 15
        if "HIGH PACE" in badges or "PASS-HEAVY" in badges:
            sc += 8
        return sc
    return max(recs, key=ceiling)
