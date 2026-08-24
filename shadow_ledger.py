"""
Shadow Ledger — accountability for every pick Shredder recommended.

At each of your draft picks we log THREE things: what Shredder's #1
recommendation was (the "shadow pick"), what you ACTUALLY drafted, and the
reasoning + ADP at that moment. That builds a parallel "Shredder shadow roster"
right next to your real one.

Then, weekly, we pull each player's real fantasy points for the week and tally
both rosters — so you can see, in black and white: "if I'd drafted the team
Shredder proposed, I'd have scored X; my real team scored Y." Cumulative,
week by week, with per-player hit/miss so you see where the copilot was right.

Persistence: data/shadow_ledger.json. Structure:
  {
    "league_id": 77269, "season": 2026, "created": "...",
    "picks": [ {overall, round, shredder_pick, shredder_pos, shredder_adp,
                shredder_reason, actual_pick, actual_pos} ... ],
    "weekly": { "1": {"actual_pts": .., "shadow_pts": .., "delta": ..,
                      "per_player": {name: pts}}, ... }
  }
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

_DATA = os.path.join(os.path.dirname(__file__), "data")
_FILE = os.path.join(_DATA, "shadow_ledger.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load() -> dict:
    if os.path.exists(_FILE):
        try:
            with open(_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"league_id": None, "season": None, "created": _now(),
            "picks": [], "weekly": {}}


def save(led: dict) -> None:
    os.makedirs(_DATA, exist_ok=True)
    with open(_FILE, "w", encoding="utf-8") as f:
        json.dump(led, f, indent=2)


def reset(league_id=None, season=None) -> dict:
    led = {"league_id": league_id, "season": season, "created": _now(),
           "picks": [], "weekly": {}}
    save(led)
    return led


def record_pick(overall: int, round_: int,
                shredder_pick: str, shredder_pos: str,
                shredder_adp: Optional[float], shredder_reason: str,
                actual_pick: str, actual_pos: str,
                league_id=None, season=None) -> dict:
    """Log one of MY picks: what Shredder wanted vs what I took."""
    led = load()
    if league_id and not led.get("league_id"):
        led["league_id"] = league_id
    if season and not led.get("season"):
        led["season"] = season
    # de-dup by overall (re-recording a corrected pick overwrites)
    led["picks"] = [p for p in led["picks"] if p.get("overall") != overall]
    led["picks"].append({
        "overall": overall, "round": round_,
        "shredder_pick": shredder_pick, "shredder_pos": shredder_pos,
        "shredder_adp": shredder_adp, "shredder_reason": shredder_reason,
        "actual_pick": actual_pick, "actual_pos": actual_pos,
        "logged": _now(),
    })
    led["picks"].sort(key=lambda p: p.get("overall", 0))
    save(led)
    return led


def shadow_roster(led: Optional[dict] = None) -> list[tuple[str, str]]:
    led = led or load()
    return [(p["shredder_pick"], p["shredder_pos"]) for p in led.get("picks", [])
            if p.get("shredder_pick")]


def actual_roster(led: Optional[dict] = None) -> list[tuple[str, str]]:
    led = led or load()
    return [(p["actual_pick"], p["actual_pos"]) for p in led.get("picks", [])
            if p.get("actual_pick")]


def record_week(week: int, actual_pts_by_player: dict, shadow_pts_by_player: dict
                ) -> dict:
    """Store one week's scoring for both rosters. `*_by_player` map name->points.
    Best-ball style: sum ALL rostered players (simple + honest; a lineup-optimal
    variant can come later)."""
    led = load()
    a_tot = round(sum(actual_pts_by_player.values()), 1)
    s_tot = round(sum(shadow_pts_by_player.values()), 1)
    led["weekly"][str(week)] = {
        "actual_pts": a_tot, "shadow_pts": s_tot,
        "delta": round(s_tot - a_tot, 1),
        "actual_per_player": {k: round(v, 1) for k, v in actual_pts_by_player.items()},
        "shadow_per_player": {k: round(v, 1) for k, v in shadow_pts_by_player.items()},
        "scored": _now(),
    }
    save(led)
    return led


def cumulative(led: Optional[dict] = None) -> dict:
    """Season-to-date totals + per-week series for both rosters."""
    led = led or load()
    weeks = sorted(led.get("weekly", {}).items(), key=lambda kv: int(kv[0]))
    a_cum = s_cum = 0.0
    series = []
    for wk, d in weeks:
        a_cum += d["actual_pts"]
        s_cum += d["shadow_pts"]
        series.append({"week": int(wk), "actual": d["actual_pts"],
                       "shadow": d["shadow_pts"], "delta": d["delta"],
                       "actual_cum": round(a_cum, 1), "shadow_cum": round(s_cum, 1)})
    return {"series": series, "actual_total": round(a_cum, 1),
            "shadow_total": round(s_cum, 1),
            "delta_total": round(s_cum - a_cum, 1),
            "weeks_scored": len(series)}


def per_player_verdict(led: Optional[dict] = None) -> list[dict]:
    """For each pick, season-to-date points the shadow player has scored vs the
    actual player, so you see where Shredder's call beat yours."""
    led = led or load()
    # aggregate weekly per-player points
    sp, ap = {}, {}
    for d in led.get("weekly", {}).values():
        for k, v in (d.get("shadow_per_player") or {}).items():
            sp[k] = sp.get(k, 0.0) + v
        for k, v in (d.get("actual_per_player") or {}).items():
            ap[k] = ap.get(k, 0.0) + v
    out = []
    for pk in led.get("picks", []):
        s_name, a_name = pk.get("shredder_pick"), pk.get("actual_pick")
        s_pts = round(sp.get(s_name, 0.0), 1)
        a_pts = round(ap.get(a_name, 0.0), 1)
        verdict = ("SAME PICK" if s_name == a_name else
                   ("SHREDDER WON" if s_pts > a_pts else
                    ("YOU WON" if a_pts > s_pts else "TIE")))
        out.append({"overall": pk.get("overall"), "round": pk.get("round"),
                    "shredder": s_name, "shredder_pts": s_pts,
                    "actual": a_name, "actual_pts": a_pts, "verdict": verdict})
    return out
