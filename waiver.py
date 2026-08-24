"""
Waiver Wire — the in-season bridge after the draft.

Given the league's rostered set (from ESPN) or a manual "already taken" list,
rank the AVAILABLE free agents by a pickup score that fuses everything the
draft copilot already knows:

  • rest-of-season value      — engine VORP over the remaining projection
  • opportunity signals       — target/snap share, O-line, pace (advanced_metrics)
  • schedule softness         — upcoming pass-D matchups (matchups)
  • injury-created opening     — a hurt starter spikes the handcuff/next man up
  • breakout / soul lean      — ascending usage the market hasn't priced (soul)
  • consistency               — weekly floor (advanced_metrics)

Also ranks YOUR bench bottom-up for DROP candidates, and emits a FAAB bid %
and a waiver-priority tier so you know how hard to chase each add.

Pure functions over the same RawPlayer pool the board uses. No new data feeds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import engine as E
import projections as P
import advanced_metrics as ADV
import matchups as M
import soul as SOUL

try:
    import injuries as INJ
except Exception:
    INJ = None


@dataclass
class WaiverTarget:
    name: str
    position: str
    team: str
    pickup_score: float
    ros_vorp: float
    faab_pct: int                 # suggested % of FAAB budget
    priority: str                 # MUST-ADD / STRONG / SPECULATIVE / STASH
    reasons: list[str] = field(default_factory=list)
    injury_note: str = ""
    rocket: bool = False          # 🚀 safe one-week streamer, high floor this week
    star: bool = False            # ⭐ ROS-beats one of MY current starters
    star_note: str = ""           # e.g. "projected to outscore your WR2 (Adams)"
    ros_points: float = 0.0       # season projection total (for STAR compare)
    icons: list[tuple[str, str]] = field(default_factory=list)  # [(emoji+label, tooltip)]


@dataclass
class DropCandidate:
    name: str
    position: str
    reason: str
    keep_score: float


def _opportunity(name: str, pos: str) -> tuple[float, list[str]]:
    delta, badges = ADV.metric_adjustments(name, pos)
    reasons = [b for b in badges if any(k in b for k in
               ("target", "snap", "air-yards", "PACE", "PASS", "o-line", "RZ"))]
    return delta, reasons[:2]


def _matchup_bump(team: str, pos: str) -> tuple[float, Optional[str]]:
    if pos not in ("QB", "WR", "TE"):
        return 0.0, None
    rep = M.schedule_report(team)
    if not rep:
        return 0.0, None
    # soft upcoming pass defenses help pass-catchers/QBs
    if rep.season_softness >= 2.0:
        return min(6.0, rep.season_softness), f"soft schedule ({rep.grade.split()[0]})"
    return 0.0, None


def _status_of(name: str) -> tuple[Optional[str], str]:
    """Return (chip, narrative) for a player's injury, or (None, '')."""
    if INJ is None:
        return None, ""
    try:
        inj = INJ.injury_for(name)
    except Exception:
        return None, ""
    if not inj:
        return None, ""
    return getattr(inj, "chip", None), getattr(inj, "narrative", "") or ""


def _injury_opening(raw: P.RawPlayer) -> tuple[float, Optional[str]]:
    """A player who is HEALTHY while being a backup/handcuff in a shaky room is
    a waiver gem; a player who is himself hurt is penalized."""
    chip, _narr = _status_of(raw.name)
    if chip in ("O", "IR", "PUP", "SUS", "D"):
        return -20.0, f"hurt ({chip}) — stash only"
    if chip == "Q":
        return -4.0, "questionable"
    return 0.0, None


_FLEX_POS = ("RB", "WR", "TE")


def my_starters_projection(my_roster: list[tuple[str, str]],
                           pool: list[P.RawPlayer], cfg: E.LeagueConfig
                           ) -> dict:
    """Build my STARTING lineup from the roster and return, per position, the
    projected points of the WEAKEST current starter (the one a free agent must
    beat to be a STAR). FLEX-eligible positions also expose a flex floor.

    Returns {"by_pos": {pos: [(name, pts) sorted desc]},
             "weakest_starter": {pos: (name, pts)},   # weakest at that pos
             "flex_floor": (name, pts) | None}         # weakest flex starter
    """
    by_name = {p.name: p for p in pool}
    proj: dict[str, list[tuple[str, float]]] = {}
    for nm, pos in my_roster:
        raw = by_name.get(nm)
        pts = E.project_points(raw.stats, cfg.scoring) if raw else 0.0
        proj.setdefault(pos, []).append((nm, pts))
    for pos in proj:
        proj[pos].sort(key=lambda x: x[1], reverse=True)

    starters = cfg.starters or {}
    weakest: dict[str, tuple[str, float]] = {}
    used: set[str] = set()
    # dedicated position starters first
    for pos, cnt in starters.items():
        if pos in ("FLEX", "SUPERFLEX", "K", "DST"):
            continue
        picks = proj.get(pos, [])[:cnt]
        for nm, _ in picks:
            used.add(nm)
        if picks:
            weakest[pos] = picks[-1]          # weakest starter at this pos
    # FLEX: weakest starter drawn from remaining RB/WR/TE
    flex_floor = None
    flex_cnt = starters.get("FLEX", 0) + starters.get("SUPERFLEX", 0)
    if flex_cnt:
        pool_flex = []
        for pos in _FLEX_POS:
            for nm, pts in proj.get(pos, []):
                if nm not in used:
                    pool_flex.append((nm, pts, pos))
        pool_flex.sort(key=lambda x: x[1], reverse=True)
        flex_starters = pool_flex[:flex_cnt]
        if flex_starters:
            fn, fp, _ = flex_starters[-1]
            flex_floor = (fn, fp)
    return {"by_pos": proj, "weakest_starter": weakest, "flex_floor": flex_floor}


def _fa_ros_points(raw: P.RawPlayer, cfg: E.LeagueConfig) -> float:
    return E.project_points(raw.stats, cfg.scoring)


def _classify_star(raw: P.RawPlayer, fa_pts: float, starters_info: dict
                   ) -> tuple[bool, str]:
    """⭐ STAR = this FA's ROS projection legitimately BEATS a starter I'm
    actually rostering — either the weakest starter at his position, or (if
    he's FLEX-eligible) my flex starter. Not 'beats replacement' — beats a guy
    in my lineup."""
    pos = raw.position
    best_beat = None
    ws = starters_info.get("weakest_starter", {}).get(pos)
    if ws and fa_pts > ws[1] + 3:            # +3 pt cushion = "legitimately"
        best_beat = (ws[0], ws[1], f"your {pos}")
    if pos in _FLEX_POS:
        ff = starters_info.get("flex_floor")
        if ff and fa_pts > ff[1] + 3 and (best_beat is None or ff[1] < best_beat[1]):
            best_beat = (ff[0], ff[1], "your FLEX")
    if best_beat:
        margin = fa_pts - best_beat[1]
        return True, (f"projected to outscore {best_beat[2]} "
                      f"({best_beat[0]}) by ~{margin:.0f} pts ROS")
    return False, ""


def _classify_rocket(raw: P.RawPlayer, opp_reasons: list[str],
                     matchup_reason: Optional[str], injury_note: str,
                     consistency_badge: Optional[str]) -> bool:
    """🚀 ROCKET = a safe ONE-WEEK streamer: high floor for the immediate week.
    Needs a secure role (workhorse snaps / real target share) AND a favorable
    setup (soft matchup or steady floor), and must NOT be hurt."""
    if raw.position in ("K", "DST"):
        # a K/DST in a great matchup is a classic streamer rocket
        return bool(matchup_reason)
    if injury_note and ("hurt" in injury_note or "stash" in injury_note):
        return False
    secure_role = any(("snap" in r or "target" in r or "WORKHORSE" in r
                       or "every-down" in r) for r in opp_reasons)
    good_setup = bool(matchup_reason) or (consistency_badge
                                          and "CONSISTENT" in consistency_badge)
    return secure_role and good_setup


def classify_icons(raw: P.RawPlayer, pv: E.PlayerValue, cfg: E.LeagueConfig,
                   scoring_key: str, opp_reasons: list[str],
                   matchup_reason: Optional[str]) -> list[tuple[str, str]]:
    """Return extra signal badges [(label, tooltip)] beyond ROCKET/STAR:
      🔥 HOT HAND   — usage trending up (rising role NOW)
      🕳️ VULTURE    — handcuff one injury from a workhorse role
      📅 SMASH SPOT — elite upcoming matchup, play him this week
      🩹 BOUNCE-BACK — ramping back from injury, buy-low
      🪤 TRAP       — name value but usage says fade
      💎 DEEP STASH — buried now, elite long-term profile
    """
    icons: list[tuple[str, str]] = []
    pos = raw.position
    m = ADV.metrics_for(raw.name) or {}
    adp = P.adp_for(raw, scoring_key) or 999
    chip, narr = _status_of(raw.name)

    # 🔥 HOT HAND — real, rising role right now
    ts = m.get("target_share") or 0
    snap = m.get("snap_share") or 0
    if (ts >= 0.20 or snap >= 0.75) and pos in ("RB", "WR", "TE"):
        icons.append(("🔥 HOT HAND", "usage is real and trending up — the role is happening now"))

    # 📅 SMASH SPOT — elite upcoming matchup
    if matchup_reason and pos in ("QB", "WR", "TE"):
        rep = M.schedule_report(raw.team)
        if rep and rep.season_softness >= 3.0:
            icons.append(("📅 SMASH SPOT", f"very soft pass-D schedule ({rep.grade.split()[0]}) — start him"))

    # 🕳️ VULTURE — healthy handcuff behind a HURT starter on the same team
    if pos == "RB" and chip is None:
        for other in _teammates_hurt(raw, cfg):
            icons.append(("🕳️ VULTURE", f"handcuff — {other} is banged up; one snap from the workload"))
            break

    # 🩹 BOUNCE-BACK — currently dinged but a real player ramping back (Q/D, not IR/Out)
    if chip in ("Q", "D") and (ts >= 0.18 or (pv.vorp or 0) > 0):
        icons.append(("🩹 BOUNCE-BACK", f"ramping back ({narr or chip}) — buy-low before he's 100%"))

    # 🪤 TRAP — early ADP but weak underlying usage = name value, fade
    if adp <= 90 and pos in ("RB", "WR", "TE") and ts and ts < 0.15 and snap and snap < 0.6:
        icons.append(("🪤 TRAP", "name/ADP value but the usage says fade — save your FAAB"))

    # 💎 DEEP STASH — buried ADP but elite long-term (soul/dark-horse style upside)
    if adp >= 140 or adp == 999:
        ssc, _ = SOUL._soul_score(raw, scoring_key)
        young = (raw.age is not None and raw.age <= 24) or raw.rookie
        if young and (ssc > 0 or (pv.vorp or 0) > -20):
            icons.append(("💎 DEEP STASH", "buried now but young with real upside — long-term hold"))

    return icons


def _teammates_hurt(raw: P.RawPlayer, cfg: E.LeagueConfig) -> list[str]:
    """Names of same-team, same-position players currently carrying an O/IR/D
    chip — i.e. the starter this backup could vulture. Best-effort; empty when
    the injury feed or teammate list isn't resolvable."""
    if INJ is None:
        return []
    hurt = []
    try:
        import projections as _P
        for other in _P.load_players(prefer_live=False):
            if other.team == raw.team and other.position == raw.position \
               and other.name != raw.name:
                st, _ = _status_of(other.name)
                if st in ("O", "IR", "PUP", "D", "SUS"):
                    hurt.append(other.name)
    except Exception:
        return []
    return hurt


def score_target(raw: P.RawPlayer, pv: E.PlayerValue, cfg: E.LeagueConfig,
                 scoring_key: str, starters_info: Optional[dict] = None) -> WaiverTarget:
    reasons: list[str] = []
    score = max(0.0, pv.vorp + 40) * 0.5     # ROS value base

    opp_delta, opp_reasons = _opportunity(raw.name, raw.position)
    score += opp_delta * 1.5
    reasons += opp_reasons

    mb, mreason = _matchup_bump(raw.team, raw.position)
    score += mb
    if mreason:
        reasons.append(mreason)

    inj_delta, inj_reason = _injury_opening(raw)
    score += inj_delta
    injury_note = inj_reason or ""

    # soul / breakout lean — reward ascending usage the market hasn't priced
    ssc, ssig = SOUL._soul_score(raw, scoring_key)
    if ssc > 0:
        score += min(12.0, ssc * 0.25)
        if ssig:
            reasons.append("ascending: " + ssig[0])

    # consistency floor
    csc, cbadge = ADV.consistency_score(raw.name, raw.position)
    if cbadge and "CONSISTENT" in cbadge:
        score += 4; reasons.append("steady floor")

    # 🚀 ROCKET + ⭐ STAR classification
    fa_pts = _fa_ros_points(raw, cfg)
    rocket = _classify_rocket(raw, opp_reasons, mreason, injury_note, cbadge)
    star, star_note = (False, "")
    if starters_info:
        star, star_note = _classify_star(raw, fa_pts, starters_info)
    if star:
        score += 15; reasons.insert(0, "beats a current starter")   # push STARs up
    if rocket:
        reasons.insert(0, "safe streamer this week")

    icons = classify_icons(raw, pv, cfg, scoring_key, opp_reasons, mreason)

    # priority + FAAB from score
    if score >= 60:
        pri, faab = "MUST-ADD", 45
    elif score >= 42:
        pri, faab = "STRONG", 22
    elif score >= 28:
        pri, faab = "SPECULATIVE", 8
    else:
        pri, faab = "STASH", 2

    return WaiverTarget(
        name=raw.name, position=raw.position, team=raw.team,
        pickup_score=round(score, 1), ros_vorp=round(pv.vorp, 1),
        faab_pct=faab, priority=pri, reasons=reasons[:4], injury_note=injury_note,
        rocket=rocket, star=star, star_note=star_note, ros_points=round(fa_pts, 1),
        icons=icons)


def find_waiver_targets(pool: list[P.RawPlayer], cfg: E.LeagueConfig,
                        rostered: set, scoring_key: str = "half",
                        positions: Optional[list[str]] = None,
                        top_n: int = 40,
                        my_roster: Optional[list[tuple[str, str]]] = None
                        ) -> list[WaiverTarget]:
    """Rank AVAILABLE free agents (pool − rostered) by pickup score. When
    my_roster is given, ⭐ STAR flags a FA whose ROS projection beats one of my
    actual starters."""
    starters_info = (my_starters_projection(my_roster, pool, cfg)
                     if my_roster else None)
    avail = [p for p in pool if p.name not in rostered]
    if positions:
        avail = [p for p in avail if p.position in positions]
    pvs = []
    raw_by_name = {}
    for raw in avail:
        pts = E.project_points(raw.stats, cfg.scoring)
        pvs.append(E.PlayerValue(raw.name, raw.name, raw.position, raw.team, pts))
        raw_by_name[raw.name] = raw
    E.compute_vorp(pvs, cfg)
    targets = [score_target(raw_by_name[pv.name], pv, cfg, scoring_key,
                            starters_info=starters_info)
               for pv in pvs]
    targets.sort(key=lambda t: t.pickup_score, reverse=True)
    return targets[:top_n]


def drop_candidates(my_roster: list[tuple[str, str]], pool: list[P.RawPlayer],
                    cfg: E.LeagueConfig, scoring_key: str = "half",
                    n: int = 5) -> list[DropCandidate]:
    """Rank MY roster bottom-up: who to cut to make room. Keep-score = ROS
    value minus injury risk; lowest = drop first. Never suggests a hurt star
    over a healthy scrub without saying why."""
    by_name = {p.name: p for p in pool}
    pvs = []
    for nm, pos in my_roster:
        raw = by_name.get(nm)
        pts = E.project_points(raw.stats, cfg.scoring) if raw else {}
        pvs.append(E.PlayerValue(nm, nm, pos, raw.team if raw else "", pts))
    E.compute_vorp(pvs, cfg)
    cands = []
    for pv in pvs:
        keep = pv.vorp
        reason = f"lowest ROS value on roster (VORP {round(pv.vorp,1)})"
        _st, _ = _status_of(pv.name)
        if _st in ("O", "IR", "PUP", "SUS"):
            keep -= 30
            reason = f"{_st} — not contributing; drop if you need the slot"
        cands.append(DropCandidate(name=pv.name, position=pv.position,
                                   reason=reason, keep_score=round(keep, 1)))
    cands.sort(key=lambda c: c.keep_score)
    return cands[:n]
