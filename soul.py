"""
Draft with Soul — the pick the projections don't believe in yet.

This is NOT the Dark Horse (a buried, deep-ADP lottery ticket) and NOT the
best-available VORP name (consensus already loves those). Soul finds the player
sitting at a *reasonable, unexciting* ADP — the market has them penciled in as a
solid-but-unspectacular contributor — while EVERY underlying signal quietly
screams breakout:

  • elite / high target share (the ball is coming their way)
  • big air-yards role (downfield, TD-equity usage)
  • a good QB and a pass-leaning, up-tempo offense (context lifts everyone)
  • healthy now, and young/ascending (age curve on their side)
  • not already priced as a star (ADP hasn't caught up to the role)

The archetype is Tee Higgins: never the sexy "breakout" everyone circles, but
a WR1 target load with an elite QB, ascending, and perennially drafted a round
or two below where the usage says he belongs. You draft him with your gut AND
the numbers — that's soul.

THE SCORE rewards a big GAP between (peripheral signals point up) and
(consensus ADP is merely okay). A true stud already at a top-12 ADP gets no
soul credit — the market isn't sleeping on them. A deep-ADP flier is a Dark
Horse, not Soul. The sweet spot is the mid-round player the room undervalues.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import engine as E
import projections as P
import advanced_metrics as ADV
import combine as CMB
import venue as VEN

try:
    import injuries as INJ
except Exception:  # injuries feed optional / offline
    INJ = None


# ADP band where "soul" lives: past the elite tier (market already believes),
# but not so deep it's a dark-horse dart.
_SOUL_ADP_MIN = 18.0     # inside this = consensus stud, no soul credit
_SOUL_ADP_MAX = 130.0    # past this = dark-horse territory, not soul


@dataclass
class SoulPick:
    name: str
    position: str
    team: str
    adp: Optional[float]
    soul_score: float
    signals: list[str]     # the up-arrows
    thesis: str            # the narrative


def _signal_strength(raw: P.RawPlayer) -> tuple[float, list[str]]:
    """How loudly do the PERIPHERALS point up? (independent of ADP).
    Returns (0-100-ish strength, human signal fragments)."""
    pos = raw.position
    if pos in ("K", "DST"):
        return -1e9, []
    strength = 0.0
    sig: list[str] = []

    m = ADV.metrics_for(raw.name) or {}

    ts = m.get("target_share")
    if ts is not None and pos in ("WR", "TE", "RB"):
        if ts >= 0.26:
            strength += 26; sig.append(f"WR1 target share ({int(ts*100)}%)")
        elif ts >= 0.21:
            strength += 16; sig.append(f"strong target share ({int(ts*100)}%)")
        elif ts >= 0.17:
            strength += 8;  sig.append(f"rising target share ({int(ts*100)}%)")

    ays = m.get("air_yards_share")
    if ays is not None and pos in ("WR", "TE"):
        if ays >= 0.32:
            strength += 14; sig.append(f"downfield air-yards role ({int(ays*100)}%)")
        elif ays >= 0.26:
            strength += 8;  sig.append(f"real air-yards role ({int(ays*100)}%)")

    snap = m.get("snap_share")
    if snap is not None and snap >= 0.82 and pos in ("WR", "TE", "RB"):
        strength += 10; sig.append(f"every-down role ({int(snap*100)}% snaps)")

    # 5-YEAR FINDING: the #1 overachiever profile is an RB who owns a bellcow
    # role (high snaps + RZ work). David Johnson RB40->RB2, Javonte RB36->RB1,
    # Brian Robinson RB34->RB1 (twice) — opportunity beats draft cost. Reward the
    # RB whose workload says "lead back" even if the market hasn't priced it.
    if pos == "RB":
        _snap = snap or 0
        _rz = m.get("red_zone_share") or 0
        _ts = m.get("target_share") or 0
        if _snap >= 0.70 and _rz >= 0.45:
            strength += 16; sig.append("bellcow workload (5-yr overachiever profile)")
        elif _snap >= 0.65 and (_rz >= 0.35 or _ts >= 0.12):
            strength += 9; sig.append("lead-back workload trending up")

    rz = m.get("red_zone_share")
    if rz is not None and rz >= 0.24 and pos in ("WR", "TE"):
        strength += 8; sig.append(f"red-zone target ({int(rz*100)}%)")
    if rz is not None and rz >= 0.5 and pos == "RB":
        strength += 10; sig.append(f"goal-line back ({int(rz*100)}% RZ)")

    pr = m.get("team_pass_rate")
    if pr is not None and pr >= 0.59 and pos in ("WR", "TE", "QB"):
        strength += 6; sig.append(f"pass-leaning offense ({int(pr*100)}%)")

    pace = m.get("team_pace")
    if pace is not None and pace >= 64:
        strength += 5; sig.append(f"up-tempo ({pace} plays/g)")

    wt = m.get("vegas_win_total")
    if wt is not None and wt >= 10.5:
        strength += 6; sig.append(f"winning offense (Vegas {wt})")

    # good O-line lifts a back the market underrates
    ol = m.get("oline_rank")
    if ol is not None and pos == "RB" and ol <= 8:
        strength += 8; sig.append(f"elite o-line (#{ol})")

    # ascending age curve
    if raw.age is not None and raw.age <= 25 and not raw.rookie:
        strength += 6; sig.append(f"ascending — only {int(raw.age)}")

    # athletic ceiling (rookies/young)
    _, abadge = CMB.athletic_adjustment(raw.name, pos, raw.rookie)
    if abadge and ("ELITE" in abadge or "PLUS" in abadge):
        strength += 6; sig.append(abadge.lower())

    # dome passing environment
    vrep = VEN.venue_report(raw.team)
    if vrep and vrep.indoor_share >= 0.55 and pos in ("WR", "TE", "QB"):
        strength += 4; sig.append("dome-friendly")

    # health check — soul needs a player who is READY, not rehabbing
    if INJ is not None:
        try:
            chip = INJ.injury_for(raw.name)
            status = getattr(chip, "status", None) if chip else None
            if status in ("O", "IR", "PUP", "SUS", "D"):
                strength -= 40; sig.append(f"health flag ({status})")
            elif status == "Q":
                strength -= 6
        except Exception:
            pass

    return strength, sig


def _soul_score(raw: P.RawPlayer, scoring_key: str) -> tuple[float, list[str]]:
    """Soul = strong up-signals × the market NOT already believing.
    The gap between peripheral strength and a merely-okay ADP is the soul."""
    adp = P.adp_for(raw, scoring_key)
    if adp is None:
        adp = 999.0
    if adp < _SOUL_ADP_MIN or adp > _SOUL_ADP_MAX:
        return -1e9, []
    strength, sig = _signal_strength(raw)
    if strength <= 0:
        return -1e9, sig
    # Signal strength LEADS — soul is about the loudest peripherals. The ADP
    # position is a gentle multiplier: a merely-okay price (mid-round) earns
    # full credit, an already-elite price (early) is discounted so the market's
    # obvious studs don't win. Depth past the sweet spot adds only a light bonus
    # (deeper == market sleeping harder), never enough to beat a louder signal.
    if adp <= 24:
        market = 0.62          # early ADP: consensus already believes — discount
    elif adp <= 70:
        market = 1.0           # the soul sweet spot: fully credited
    else:
        market = 1.0 + min(0.12, (adp - 70) / 300.0)   # mild deep bonus
    score = strength * market
    return round(score, 1), sig


def find_soul(pool: list[P.RawPlayer], cfg: E.LeagueConfig,
              scoring_key: str = "half",
              drafted: Optional[set] = None) -> Optional[SoulPick]:
    """Return the single strongest 'draft with soul' candidate on the board:
    a mid-ADP player whose signals all point to excelling beyond consensus."""
    drafted = drafted or set()
    best: Optional[SoulPick] = None
    best_score = -1e18
    for raw in pool:
        if raw.name in drafted or raw.position in ("K", "DST"):
            continue
        sc, sig = _soul_score(raw, scoring_key)
        if sc > best_score and sc > 0:
            best_score = sc
            adp = P.adp_for(raw, scoring_key)
            adp_txt = f"{adp:.0f}" if adp else "undrafted"
            thesis = (
                f"{raw.name} ({raw.position}, {raw.team}) — the projections have "
                f"them as just-fine at ADP {adp_txt}, but the tape and the usage "
                f"say more: " + "; ".join(sig[:3]) + ". Nobody's calling it a "
                f"breakout — that's exactly why it is one. Draft with soul.")
            best = SoulPick(name=raw.name, position=raw.position, team=raw.team,
                            adp=adp, soul_score=round(sc, 1),
                            signals=sig, thesis=thesis)
    return best
