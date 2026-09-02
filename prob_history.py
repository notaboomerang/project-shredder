"""
Probability history — accountability + calibration for the win-prob model.

Every time the Betting Edge view refreshes, we snapshot each game's state and
BOTH probabilities (our model vs the de-vigged market). After games finish we
settle them with the real final score. Then we score the log:

  - BRIER SCORE  — mean squared error of P(fav) vs actual outcome. Lower = better.
                   This is the standard win-probability metric.
  - LOG LOSS     — penalizes confident wrong calls harder than Brier.
  - CALIBRATION  — bucket predictions (0-10%, 10-20%, ...) and check that when we
                   said 70% the favorite actually won ~70% of the time.

NORTH STAR: does our MODEL beat the MARKET on Brier? If the de-vigged book line
scores better than our model, the "edges" we surface are noise, not signal. If
we beat it, the divergences are worth paying attention to.

IMPORTANT — what this is and isn't: this LOGS and MEASURES. It does not retrain
the model. `live_win_prob` is a hand-tuned heuristic (constants like _GAME_SD,
_PTS_PER_DRIVE); improving it means re-tuning those constants, which needs a lot
of settled games to do responsibly. This module accumulates the evidence and
tells you WHICH way the model is miscalibrated so that re-tuning is grounded.

Storage: append-only JSONL at data/prob_history.jsonl (one record per game per
observation). Append-only is durable, easy to inspect, and never loses history.
"""
from __future__ import annotations

import json
import math
import os
import datetime as _dt
from typing import Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_FILE = os.path.join(_DATA_DIR, "prob_history.jsonl")

# Don't log a fresh snapshot unless the game state moved enough to matter — keeps
# the log from ballooning when the view is refreshed repeatedly on the same state.
_MIN_PROB_DELTA = 0.01      # model prob must move >=1 point, or...
_MIN_SECONDS_DELTA = 60.0   # ...>=60s of game clock must have elapsed


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _seconds_left(quarter, clock) -> Optional[float]:
    try:
        q = int(quarter or 0)
    except Exception:
        return None
    if not q:
        return 3600.0
    try:
        mm, ss = str(clock).split(":")
        qleft = int(mm) * 60 + int(ss)
    except Exception:
        qleft = 0
    return max(0.0, min(3600.0, (4 - q) * 900 + qleft))


def _read_all() -> list[dict]:
    if not os.path.exists(_FILE):
        return []
    out = []
    try:
        with open(_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
    except Exception:
        return []
    return out


def _append(rec: dict) -> None:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def _rewrite(records: list[dict]) -> None:
    """Rewrite the whole log (used by settle to stamp outcomes)."""
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        os.replace(tmp, _FILE)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 1. Snapshot
# ---------------------------------------------------------------------------

def _last_snapshot_for(event_id: str, records: Optional[list[dict]] = None
                       ) -> Optional[dict]:
    recs = records if records is not None else _read_all()
    latest = None
    for r in recs:
        if r.get("event_id") == event_id:
            latest = r  # file is append-order, so last wins
    return latest


def snapshot(games, week: Optional[int] = None) -> int:
    """Log the current model-vs-market state for each game with a usable line.

    `games` = list of live_games.LiveGame (has model_p_fav, market_p_fav, etc).
    Dedups against the last logged snapshot per event so repeated refreshes on an
    unchanged state don't bloat the log. Returns how many records were written.
    """
    existing = _read_all()
    # index last snapshot per event for cheap dedup
    last_by_event: dict[str, dict] = {}
    for r in existing:
        eid = r.get("event_id")
        if eid:
            last_by_event[eid] = r

    written = 0
    for g in games or []:
        model_p = getattr(g, "model_p_fav", None)
        market_p = getattr(g, "market_p_fav", None)
        favorite = getattr(g, "favorite", "") or ""
        if model_p is None or market_p is None or not favorite:
            continue  # nothing to score without both probs + a favorite

        eid = getattr(g, "game_id", "") or ""
        quarter = getattr(g, "quarter", 0) or 0
        clock = getattr(g, "clock", "") or ""
        sec_left = _seconds_left(quarter, clock)

        # dedup: skip if prob barely moved AND clock barely moved since last log
        prev = last_by_event.get(eid)
        if prev is not None:
            pmodel = prev.get("model_p_fav")
            psec = prev.get("seconds_left")
            prob_move = (abs(model_p - pmodel) if pmodel is not None else 1.0)
            sec_move = (abs((sec_left or 0) - (psec or 0))
                        if (sec_left is not None and psec is not None) else 9999)
            already_final = prev.get("fav_won") is not None
            if (not already_final and prob_move < _MIN_PROB_DELTA
                    and sec_move < _MIN_SECONDS_DELTA):
                continue

        rec = {
            "ts": _now_iso(),
            "week": week,
            "event_id": eid,
            "home": getattr(g, "home", ""),
            "away": getattr(g, "away", ""),
            "favorite": favorite,
            "status": getattr(g, "status", ""),
            "quarter": quarter,
            "clock": clock,
            "seconds_left": sec_left,
            "home_score": getattr(g, "home_score", None),
            "away_score": getattr(g, "away_score", None),
            "fav_spread": getattr(g, "fav_points", None),
            "model_p_fav": round(float(model_p), 4),
            "market_p_fav": round(float(market_p), 4),
            "possession_aware": getattr(g, "fav_drives_left", None) is not None,
            "possessing_team": getattr(g, "possessing_team", None),
            # settlement fields (filled later)
            "fav_won": None,
            "final_home": None,
            "final_away": None,
            "settled_ts": None,
        }
        _append(rec)
        written += 1
    return written


# ---------------------------------------------------------------------------
# 2. Settle (stamp real outcomes from ESPN finals)
# ---------------------------------------------------------------------------

def _final_scores() -> dict[str, tuple[int, int, str]]:
    """Map event_id -> (home_score, away_score, home_abbrev) for FINAL games,
    from ESPN's scoreboard (routed through espn_http). {} on failure."""
    try:
        import espn_http as _http
    except Exception:
        return {}
    data = _http.get_json(
        "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
        timeout=12)
    out: dict[str, tuple[int, int, str]] = {}
    for ev in (data.get("events") or []):
        st = ((ev.get("status") or {}).get("type") or {})
        if st.get("state") != "post":
            continue
        comp = (ev.get("competitions") or [{}])[0]
        home_ab = ""
        hs = as_ = 0
        for c in comp.get("competitors", []) or []:
            ab = (c.get("team") or {}).get("abbreviation", "")
            sc = int(c.get("score", 0) or 0)
            if c.get("homeAway") == "home":
                home_ab, hs = ab, sc
            else:
                as_ = sc
        out[ev.get("id", "")] = (hs, as_, home_ab)
    return out


def settle(finals: Optional[dict] = None) -> int:
    """Stamp any unsettled records whose game is now final with fav_won (1/0).
    Returns how many records were newly settled. Idempotent."""
    records = _read_all()
    if not records:
        return 0
    if finals is None:
        finals = _final_scores()
    if not finals:
        return 0

    settled = 0
    for r in records:
        if r.get("fav_won") is not None:
            continue
        eid = r.get("event_id")
        if eid not in finals:
            continue
        hs, as_, home_ab = finals[eid]
        if hs == as_:
            continue  # tie: leave unsettled (rare; not a fav win or loss)
        home_won = hs > as_
        # did the favorite win? favorite is a team abbrev matching home or away.
        fav = r.get("favorite", "")
        fav_is_home = (fav == r.get("home")) or (fav == home_ab)
        fav_won = 1 if (home_won == fav_is_home) else 0
        r["fav_won"] = fav_won
        r["final_home"] = hs
        r["final_away"] = as_
        r["settled_ts"] = _now_iso()
        settled += 1

    if settled:
        _rewrite(records)
    return settled


# ---------------------------------------------------------------------------
# 3. Scoring — Brier, log loss, calibration, model vs market
# ---------------------------------------------------------------------------

def _brier(p: float, y: int) -> float:
    return (p - y) ** 2


def _logloss(p: float, y: int) -> float:
    eps = 1e-9
    p = min(1 - eps, max(eps, p))
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def score(kind: str = "all") -> dict:
    """Score settled records. `kind`: 'all' | 'pregame' | 'live' | 'poss_aware'.

    Returns a dict with sample size, model/market Brier + log-loss, the verdict
    (does the model beat the market on Brier?), and a calibration table.
    """
    recs = [r for r in _read_all() if r.get("fav_won") in (0, 1)]

    if kind == "pregame":
        recs = [r for r in recs if (r.get("seconds_left") or 0) >= 3599]
    elif kind == "live":
        recs = [r for r in recs if (r.get("seconds_left") or 3600) < 3599]
    elif kind == "poss_aware":
        recs = [r for r in recs if r.get("possession_aware")]

    n = len(recs)
    result = {
        "kind": kind, "n": n,
        "model_brier": None, "market_brier": None,
        "model_logloss": None, "market_logloss": None,
        "brier_delta": None,          # market - model (positive = model better)
        "verdict": "not enough settled games yet",
        "calibration": [],
    }
    if n == 0:
        return result

    mb = sum(_brier(r["model_p_fav"], r["fav_won"]) for r in recs) / n
    kb = sum(_brier(r["market_p_fav"], r["fav_won"]) for r in recs) / n
    ml = sum(_logloss(r["model_p_fav"], r["fav_won"]) for r in recs) / n
    kl = sum(_logloss(r["market_p_fav"], r["fav_won"]) for r in recs) / n
    result.update(model_brier=round(mb, 4), market_brier=round(kb, 4),
                  model_logloss=round(ml, 4), market_logloss=round(kl, 4),
                  brier_delta=round(kb - mb, 4))

    if n < 30:
        result["verdict"] = (f"model Brier {mb:.3f} vs market {kb:.3f} — "
                             f"only {n} games, too few to trust")
    elif mb < kb:
        result["verdict"] = (f"MODEL beats market on Brier "
                             f"({mb:.3f} < {kb:.3f}) — edges look real")
    elif mb > kb:
        result["verdict"] = (f"market beats model on Brier "
                             f"({kb:.3f} < {mb:.3f}) — edges are likely noise")
    else:
        result["verdict"] = f"model and market tied ({mb:.3f})"

    # calibration table (deciles) on the MODEL probability
    buckets = []
    for lo in range(0, 100, 10):
        hi = lo + 10
        lo_f, hi_f = lo / 100.0, hi / 100.0
        b = [r for r in recs
             if lo_f <= r["model_p_fav"] < hi_f
             or (hi == 100 and r["model_p_fav"] == 1.0)]
        if not b:
            continue
        pred = sum(r["model_p_fav"] for r in b) / len(b)
        actual = sum(r["fav_won"] for r in b) / len(b)
        buckets.append({
            "bucket": f"{lo}-{hi}%", "n": len(b),
            "predicted": round(pred, 3), "actual": round(actual, 3),
            "gap": round(actual - pred, 3),
        })
    result["calibration"] = buckets
    return result


def stats() -> dict:
    """Quick counts for the UI header."""
    recs = _read_all()
    settled = [r for r in recs if r.get("fav_won") in (0, 1)]
    events = {r.get("event_id") for r in recs}
    return {
        "total_records": len(recs),
        "settled_records": len(settled),
        "distinct_events": len(events),
        "file": _FILE,
    }
