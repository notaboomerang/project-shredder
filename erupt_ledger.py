"""Eruption prediction ledger.

Records every erupt call the app makes — player, week, projected ceiling boost,
and the supporting-cast note — so the "you said X would smash" claim becomes
VERIFIABLE. Later, once real weekly points are available (via
EspnClient.player_week_points), each logged call is scored a HIT / MISS so the
user can see the model's actual hit-rate instead of trusting vibes.

Storage: data/erupt_ledger.json — a flat list of records, newest appended.
Idempotent per (season, week, player): re-logging the same call updates it
rather than duplicating, so re-running Eruption Watch in a week doesn't inflate
the ledger.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_LEDGER = os.path.join(_DATA_DIR, "erupt_ledger.json")

# A logged ERUPT call HITs if actual points clear this "smash" bar (position-aware).
_HIT_THRESHOLD = {"QB": 22.0, "RB": 18.0, "WR": 17.0, "TE": 14.0,
                  "K": 12.0, "DST": 12.0}
_HIT_DEFAULT = 16.0

# A logged STINKER call (predicted to bust) HITs if actual points fall BELOW this
# floor — i.e. the player genuinely stunk as predicted. Position-aware.
_STINKER_FLOOR = {"QB": 14.0, "RB": 9.0, "WR": 8.0, "TE": 6.0,
                  "K": 6.0, "DST": 5.0}
_STINKER_DEFAULT = 8.0


@dataclass
class EruptCall:
    season: int
    week: int
    player: str
    position: str
    team: str
    opponent: str
    ceiling_boost: float
    kind: str = "erupt"               # "erupt" (smash) | "stinker" (bust)
    cast_quality: str = ""            # QB/supporting-cast note at call time
    note: str = ""
    logged_at: str = ""               # ISO timestamp
    actual_points: Optional[float] = None   # filled in when scored
    result: str = ""                  # "" | "HIT" | "MISS"
    threshold: Optional[float] = None       # the bar used to grade (for transparency)


def _read() -> list[dict]:
    if not os.path.exists(_LEDGER):
        return []
    try:
        with open(_LEDGER, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write(records: list[dict]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)
    os.replace(tmp, _LEDGER)      # atomic-ish; avoids a half-written ledger


def _key(rec: dict) -> tuple:
    return (rec.get("season"), rec.get("week"), (rec.get("player") or "").lower(),
            rec.get("kind", "erupt"))


def log_calls(spots, season: int, week: int, min_boost: float = 5.0,
              top_n: int = 15, kind: str = "erupt") -> int:
    """Record the top eruption spots for a week. `spots` are EruptionSpot-like
    objects (or dicts) with player/position/team/opponent/ceiling_boost/
    cast_quality/note. `kind` is "erupt" (predicted smash) or "stinker"
    (predicted bust). Idempotent per (season, week, player, kind). Returns the
    number of records written/updated. Never raises."""
    try:
        existing = _read()
        by_key = {_key(r): r for r in existing}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        n = 0
        ranked = sorted(spots or [],
                        key=lambda s: _get(s, "ceiling_boost", 0.0),
                        reverse=True)
        for s in ranked[:top_n]:
            boost = float(_get(s, "ceiling_boost", 0.0) or 0.0)
            if boost < min_boost:
                continue
            call = EruptCall(
                season=int(season), week=int(week),
                player=_get(s, "player", ""), position=_get(s, "position", ""),
                team=_get(s, "team", ""), opponent=_get(s, "opponent", ""),
                ceiling_boost=round(boost, 2),
                kind=kind,
                cast_quality=_get(s, "cast_quality", "") or "",
                note=_get(s, "note", "") or "",
                logged_at=now,
            )
            rec = asdict(call)
            k = _key(rec)
            if k in by_key:
                # preserve any already-scored result/actual
                prev = by_key[k]
                rec["actual_points"] = prev.get("actual_points")
                rec["result"] = prev.get("result", "")
                rec["threshold"] = prev.get("threshold")
                rec["logged_at"] = prev.get("logged_at", now)
                by_key[k].update(rec)
            else:
                by_key[k] = rec
                existing.append(rec)
            n += 1
        _write(existing)
        return n
    except Exception:
        return 0


def log_stinkers(spots, season: int, week: int, min_boost: float = 5.0,
                 top_n: int = 15) -> int:
    """Record the top STINKER calls (players predicted to bust) for a week.
    Same shape as log_calls; `ceiling_boost` here is the magnitude of the
    downgrade signal. Idempotent per (season, week, player, kind='stinker')."""
    return log_calls(spots, season, week, min_boost=min_boost, top_n=top_n,
                     kind="stinker")


def score_week(points_by_norm_name: dict, season: int, week: int,
               norm_fn=None) -> int:
    """Grade the logged calls for a week against actual points. `points_by_norm_
    name` = {normalized_name: fantasy_points} (from EspnClient.player_week_points).
    `norm_fn` normalizes a player name to match that dict's keys (defaults to a
    light lower/strip). Returns the number of calls scored. Never raises."""
    try:
        if norm_fn is None:
            def norm_fn(s):     # noqa: E306
                return (s or "").lower().strip()
        recs = _read()
        scored = 0
        for r in recs:
            if r.get("season") != int(season) or r.get("week") != int(week):
                continue
            pts = points_by_norm_name.get(norm_fn(r.get("player", "")))
            if pts is None:
                continue
            pos = (r.get("position") or "").upper()
            kind = r.get("kind", "erupt")
            pts = float(pts)
            if kind == "stinker":
                # HIT when the player busted (scored BELOW the floor) as predicted.
                floor = _STINKER_FLOOR.get(pos, _STINKER_DEFAULT)
                r["threshold"] = floor
                r["result"] = "HIT" if pts <= floor else "MISS"
            else:
                # erupt: HIT when the player smashed (cleared the bar).
                thr = _HIT_THRESHOLD.get(pos, _HIT_DEFAULT)
                r["threshold"] = thr
                r["result"] = "HIT" if pts >= thr else "MISS"
            r["actual_points"] = round(pts, 2)
            scored += 1
        _write(recs)
        return scored
    except Exception:
        return 0


def load(season: Optional[int] = None, week: Optional[int] = None) -> list[dict]:
    """Return logged calls, optionally filtered by season/week, newest first."""
    recs = _read()
    if season is not None:
        recs = [r for r in recs if r.get("season") == int(season)]
    if week is not None:
        recs = [r for r in recs if r.get("week") == int(week)]
    return sorted(recs, key=lambda r: (r.get("week", 0), r.get("logged_at", "")),
                  reverse=True)


def hit_rate(season: Optional[int] = None, week: Optional[int] = None) -> dict:
    """Accuracy summary of scored calls, broken out by call kind so eruption and
    stinker accuracy are tracked SEPARATELY. Returns overall plus a per-kind
    split: {overall:{...}, by_kind:{erupt:{...}, stinker:{...}}}."""
    recs = load(season=season, week=week)

    def _summ(rows):
        scored = [r for r in rows if r.get("result") in ("HIT", "MISS")]
        hits = sum(1 for r in scored if r["result"] == "HIT")
        pending = sum(1 for r in rows if not r.get("result"))
        n = len(scored)
        return {
            "scored": n,
            "hits": hits,
            "misses": n - hits,
            "hit_rate": round(hits / n, 3) if n else None,
            "pending": pending,
            "total": len(rows),
        }

    erupts = [r for r in recs if r.get("kind", "erupt") == "erupt"]
    stinkers = [r for r in recs if r.get("kind") == "stinker"]
    return {
        "overall": _summ(recs),
        "by_kind": {"erupt": _summ(erupts), "stinker": _summ(stinkers)},
    }


def _get(obj, attr, default=None):
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)
