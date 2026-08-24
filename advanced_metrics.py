"""
Advanced metrics module — opportunity, efficiency, environment, risk.

Adds the "opportunity is stickier than efficiency" edges on top of raw
projections: target/air-yards/snap/red-zone share, O-line run blocking, team
pace & pass rate, injury risk, and Vegas win total. These NUDGE the composite
and emit human-readable badges; they never override projection + VORP.

DATA POSTURE: seeded illustrative values for ~20 notable 2026 players,
overridable via data/advanced.json. Swap in live feeds (nflverse, Vegas) when
wired; treat the seed as a fallback, not frozen truth.
"""
from __future__ import annotations

import json
import os
from typing import Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_OVERRIDE = os.path.join(_DATA_DIR, "advanced.json")

# name -> metrics. All shares 0-1; oline_rank 1(best)-32(worst); pace plays/game;
# pass_rate 0-1; injury_risk 0-1 (higher = riskier); win_total season wins.
_SEED = {
    "Bijan Robinson":     {"target_share": 0.14, "snap_share": 0.82, "red_zone_share": 0.55, "oline_rank": 12, "team_pace": 63, "team_pass_rate": 0.58, "injury_risk": 0.18, "vegas_win_total": 9.5},
    "Jahmyr Gibbs":       {"target_share": 0.15, "snap_share": 0.68, "red_zone_share": 0.42, "oline_rank": 4,  "team_pace": 65, "team_pass_rate": 0.57, "injury_risk": 0.20, "vegas_win_total": 11.5},
    "Saquon Barkley":     {"target_share": 0.09, "snap_share": 0.78, "red_zone_share": 0.60, "oline_rank": 2,  "team_pace": 62, "team_pass_rate": 0.55, "injury_risk": 0.28, "vegas_win_total": 12.5},
    "Christian McCaffrey":{"target_share": 0.18, "snap_share": 0.80, "red_zone_share": 0.58, "oline_rank": 9,  "team_pace": 64, "team_pass_rate": 0.58, "injury_risk": 0.45, "vegas_win_total": 11.5},
    "Derrick Henry":      {"target_share": 0.04, "snap_share": 0.62, "red_zone_share": 0.68, "oline_rank": 15, "team_pace": 61, "team_pass_rate": 0.52, "injury_risk": 0.30, "vegas_win_total": 11.5},
    "Chase Brown":        {"target_share": 0.12, "snap_share": 0.70, "red_zone_share": 0.48, "oline_rank": 22, "team_pace": 66, "team_pass_rate": 0.62, "injury_risk": 0.20, "vegas_win_total": 9.5},
    "Kenneth Walker III": {"target_share": 0.11, "snap_share": 0.60, "red_zone_share": 0.45, "oline_rank": 18, "team_pace": 63, "team_pass_rate": 0.57, "injury_risk": 0.30, "vegas_win_total": 9.5},
    "Ja'Marr Chase":      {"target_share": 0.30, "air_yards_share": 0.38, "snap_share": 0.92, "red_zone_share": 0.28, "team_pace": 66, "team_pass_rate": 0.62, "injury_risk": 0.15, "vegas_win_total": 9.5},
    "Puka Nacua":         {"target_share": 0.28, "air_yards_share": 0.30, "snap_share": 0.88, "red_zone_share": 0.22, "team_pace": 63, "team_pass_rate": 0.58, "injury_risk": 0.25, "vegas_win_total": 10.5},
    "Justin Jefferson":   {"target_share": 0.29, "air_yards_share": 0.36, "snap_share": 0.91, "red_zone_share": 0.24, "team_pace": 62, "team_pass_rate": 0.60, "injury_risk": 0.15, "vegas_win_total": 9.5},
    "CeeDee Lamb":        {"target_share": 0.29, "air_yards_share": 0.34, "snap_share": 0.90, "red_zone_share": 0.25, "team_pace": 63, "team_pass_rate": 0.61, "injury_risk": 0.15, "vegas_win_total": 8.5},
    "Amon-Ra St. Brown":  {"target_share": 0.27, "air_yards_share": 0.22, "snap_share": 0.90, "red_zone_share": 0.30, "team_pace": 65, "team_pass_rate": 0.57, "injury_risk": 0.12, "vegas_win_total": 11.5},
    "Nico Collins":       {"target_share": 0.26, "air_yards_share": 0.34, "snap_share": 0.86, "red_zone_share": 0.24, "team_pace": 62, "team_pass_rate": 0.59, "injury_risk": 0.20, "vegas_win_total": 9.5},
    "Drake London":       {"target_share": 0.27, "air_yards_share": 0.32, "snap_share": 0.88, "red_zone_share": 0.26, "team_pace": 63, "team_pass_rate": 0.58, "injury_risk": 0.15, "vegas_win_total": 9.5},
    "Davante Adams":      {"target_share": 0.24, "air_yards_share": 0.30, "snap_share": 0.85, "red_zone_share": 0.28, "team_pace": 63, "team_pass_rate": 0.58, "injury_risk": 0.28, "vegas_win_total": 10.5},
    "George Pickens":     {"target_share": 0.23, "air_yards_share": 0.36, "snap_share": 0.84, "red_zone_share": 0.20, "team_pace": 63, "team_pass_rate": 0.61, "injury_risk": 0.18, "vegas_win_total": 8.5},
    "Tee Higgins":        {"target_share": 0.26, "air_yards_share": 0.34, "snap_share": 0.85, "red_zone_share": 0.27, "team_pace": 66, "team_pass_rate": 0.62, "injury_risk": 0.20, "vegas_win_total": 9.5},
    "DK Metcalf":         {"target_share": 0.25, "air_yards_share": 0.35, "snap_share": 0.86, "red_zone_share": 0.26, "team_pace": 63, "team_pass_rate": 0.59, "injury_risk": 0.15, "vegas_win_total": 9.5},
    "Jaylen Waddle":      {"target_share": 0.24, "air_yards_share": 0.30, "snap_share": 0.85, "red_zone_share": 0.22, "team_pace": 65, "team_pass_rate": 0.60, "injury_risk": 0.18, "vegas_win_total": 8.5},
    "Brock Bowers":       {"target_share": 0.24, "air_yards_share": 0.18, "snap_share": 0.86, "red_zone_share": 0.22, "team_pace": 62, "team_pass_rate": 0.60, "injury_risk": 0.15, "vegas_win_total": 6.5},
    "Trey McBride":       {"target_share": 0.26, "air_yards_share": 0.16, "snap_share": 0.88, "red_zone_share": 0.20, "team_pace": 63, "team_pass_rate": 0.60, "injury_risk": 0.15, "vegas_win_total": 8.5},
    "Josh Allen":         {"snap_share": 1.0, "team_pace": 64, "team_pass_rate": 0.58, "injury_risk": 0.12, "vegas_win_total": 11.5},
    "Matthew Stafford":   {"snap_share": 1.0, "team_pace": 63, "team_pass_rate": 0.58, "injury_risk": 0.35, "vegas_win_total": 10.5},
}


def load_metrics() -> dict:
    if os.path.exists(_OVERRIDE):
        with open(_OVERRIDE, encoding="utf-8") as f:
            return json.load(f)
    return dict(_SEED)


def metrics_for(name: str) -> Optional[dict]:
    return load_metrics().get(name)


def metric_adjustments(name: str, position: str) -> tuple[float, list[str]]:
    """Composite nudge + badges from advanced metrics. Modest magnitude."""
    m = metrics_for(name)
    if not m:
        return 0.0, []
    delta = 0.0
    badges: list[str] = []

    ts = m.get("target_share")
    if ts is not None and position in ("WR", "TE", "RB"):
        if ts >= 0.27:
            delta += 5; badges.append(f"ELITE target share ({int(ts*100)}%)")
        elif ts >= 0.22:
            delta += 3; badges.append(f"HIGH target share ({int(ts*100)}%)")

    ays = m.get("air_yards_share")
    if ays is not None and ays >= 0.34 and position in ("WR", "TE"):
        delta += 2; badges.append(f"BIG air-yards role ({int(ays*100)}%)")

    snap = m.get("snap_share")
    if snap is not None and snap >= 0.85 and position in ("WR", "TE", "RB"):
        delta += 2; badges.append(f"WORKHORSE snaps ({int(snap*100)}%)")

    rz = m.get("red_zone_share")
    if rz is not None and rz >= 0.55 and position == "RB":
        delta += 4; badges.append(f"ELITE RZ role ({int(rz*100)}%)")

    ol = m.get("oline_rank")
    if ol is not None and position == "RB":
        if ol <= 8:
            delta += 3; badges.append(f"ELITE o-line (#{ol})")
        elif ol >= 25:
            delta -= 3; badges.append(f"BAD o-line (#{ol})")

    pace = m.get("team_pace")
    if pace is not None and pace >= 65 and position in ("QB", "WR", "TE", "RB"):
        delta += 2; badges.append(f"HIGH pace ({pace} plays/g)")

    pr = m.get("team_pass_rate")
    if pr is not None and pr >= 0.61 and position in ("WR", "TE", "QB"):
        delta += 2; badges.append(f"PASS-heavy offense ({int(pr*100)}%)")

    inj = m.get("injury_risk")
    if inj is not None and inj >= 0.35:
        delta -= 3; badges.append(f"INJURY risk ({int(inj*100)}%)")

    wt = m.get("vegas_win_total")
    if wt is not None and wt >= 11.5:
        delta += 2; badges.append(f"HIGH team total (Vegas {wt} wins)")
    elif wt is not None and wt <= 6.5:
        delta -= 1; badges.append(f"LOW team total (Vegas {wt} wins)")

    return round(delta, 1), badges


def consistency_score(name: str, position: str) -> tuple[float, Optional[str]]:
    """Estimate week-to-week RELIABILITY (0-100) from role stability. High
    snap+target share and volume-based scoring = steady weekly floor; low-share,
    TD-dependent, or committee roles = boom/bust. Returns (score, badge)."""
    m = metrics_for(name)
    if not m:
        return 50.0, None
    score = 50.0
    snap = m.get("snap_share")
    ts = m.get("target_share")
    ays = m.get("air_yards_share")
    rz = m.get("red_zone_share")
    if snap is not None:
        score += (snap - 0.6) * 60           # heavy snaps = steady
    if ts is not None and position in ("WR", "TE", "RB"):
        score += (ts - 0.18) * 80            # target volume = PPR floor
    # deep-threat / air-yards heavy = more volatile (boom/bust)
    if ays is not None and ays >= 0.36:
        score -= 8
    # pure TD-dependence (low targets but high RZ) = volatile
    if position == "RB" and rz is not None and rz >= 0.6 and (ts or 0) < 0.1:
        score -= 6
    score = max(0.0, min(100.0, score))
    if score >= 70:
        return round(score, 0), f"CONSISTENT (floor {int(score)})"
    if score <= 35:
        return round(score, 0), f"VOLATILE (boom/bust {int(score)})"
    return round(score, 0), None



# --------------------------------------------------------------------------- K / DST team context
# A kicker's fantasy value is OPPORTUNITY, not leg: a team that kicks FGs (low
# 4th-down aggressiveness) and stalls in the red zone (low RZ-TD rate) but scores
# a lot generates the most FG attempts. Seeded team tendencies, override via
# data/team_context.json. Fields:
#   go_rate      0-1  how often they GO on 4th down (high = fewer FGs for the K)
#   rz_td_rate   0-1  red-zone TD conversion (high = fewer FGs, more XPs)
#   scoring_vol  ~pts/game proxy (higher = more scoring chances overall)
# fg_friendliness = scoring_vol lift × (1 - go_rate lean) × (RZ-stall lean)
import json as _json

_TEAM_CTX_SEED = {
    # aggressive go-for-it staffs -> FEWER FGs for their kicker
    "DET": {"go_rate": 0.42, "rz_td_rate": 0.68, "scoring_vol": 28},
    "PHI": {"go_rate": 0.40, "rz_td_rate": 0.64, "scoring_vol": 26},
    "BAL": {"go_rate": 0.38, "rz_td_rate": 0.66, "scoring_vol": 27},
    "BUF": {"go_rate": 0.34, "rz_td_rate": 0.65, "scoring_vol": 28},
    "KC":  {"go_rate": 0.30, "rz_td_rate": 0.60, "scoring_vol": 27},
    "SF":  {"go_rate": 0.28, "rz_td_rate": 0.62, "scoring_vol": 26},
    # FG-friendly: score a lot but settle (great for the kicker)
    "DAL": {"go_rate": 0.20, "rz_td_rate": 0.52, "scoring_vol": 25},
    "MIA": {"go_rate": 0.19, "rz_td_rate": 0.53, "scoring_vol": 24},
    "LAR": {"go_rate": 0.22, "rz_td_rate": 0.55, "scoring_vol": 24},
    "GB":  {"go_rate": 0.21, "rz_td_rate": 0.54, "scoring_vol": 25},
    "TB":  {"go_rate": 0.20, "rz_td_rate": 0.53, "scoring_vol": 24},
    "HOU": {"go_rate": 0.23, "rz_td_rate": 0.55, "scoring_vol": 24},
    "CIN": {"go_rate": 0.24, "rz_td_rate": 0.57, "scoring_vol": 26},
}
_TEAM_CTX_OVERRIDE = os.path.join(_DATA_DIR, "team_context.json")


def _team_ctx() -> dict:
    if os.path.exists(_TEAM_CTX_OVERRIDE):
        try:
            with open(_TEAM_CTX_OVERRIDE, encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            return dict(_TEAM_CTX_SEED)
    return dict(_TEAM_CTX_SEED)


def kicker_context(team: str) -> tuple[float, Optional[str]]:
    """Return (fg_opportunity_multiplier, note) for a team's kicker.
    >1.0 = more FG chances (conservative, high-scoring, stalls in RZ);
    <1.0 = fewer (aggressive go-for-it staff or elite RZ-TD offense).
    Unknown team -> (1.0, None)."""
    c = _team_ctx().get((team or "").upper())
    if not c:
        return 1.0, None
    # scoring volume lift around a ~23 pts/g baseline
    vol = c.get("scoring_vol", 23) / 23.0
    # go-for-it: 0.30 baseline; higher go_rate suppresses FGs
    go = 1.0 - (c.get("go_rate", 0.28) - 0.28) * 1.4
    # RZ stalls (low TD rate) -> more FGs; 0.58 baseline
    stall = 1.0 + (0.58 - c.get("rz_td_rate", 0.58)) * 1.2
    mult = max(0.6, min(1.5, vol * go * stall))
    if mult >= 1.12:
        note = "FG-friendly offense (kicks, stalls, scores)"
    elif mult <= 0.9:
        note = "go-for-it offense (fewer FG chances)"
    else:
        note = None
    return round(mult, 3), note


def dst_context(team: str) -> tuple[float, Optional[str]]:
    """Light DST context: a high-scoring team's DST tends to play with leads
    (more pass-rush/INT chances). Reuses scoring_vol as a weak proxy. Neutral
    (1.0) for unknown teams — real DST value is opponent/scheme, seeded via ADP."""
    c = _team_ctx().get((team or "").upper())
    if not c:
        return 1.0, None
    vol = c.get("scoring_vol", 23) / 23.0
    mult = max(0.85, min(1.2, 0.9 + (vol - 1.0) * 0.6))
    return round(mult, 3), ("plays with leads" if mult >= 1.08 else None)
