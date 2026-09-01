"""
Project Shredder -- app-side reader for the DOM live-sync feed.

The _dom_live_poller.py process writes data/live_picks.json while the ESPN draft
room is open. This module lets the Streamlit app tail that file and return the
picks it has not yet ingested, resolved to pool player names, with a flag for
whether the pick is the user's own (by matching the pick's owner label to the
user's team name).

The app calls read_new_picks(pool, already_drafted, my_team_name) each poll and
feeds the results through its existing _record_pick(name, position, mine) path.
"""
from __future__ import annotations

import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_FEED = os.path.join(_HERE, "data", "live_picks.json")


def _norm(s: str) -> str:
    """Normalize a player name for matching. Strips generational SUFFIX TOKENS
    (Jr/Sr/II/III/IV/V) BEFORE removing punctuation, then lowercases alnum only.
    Without the token strip, 'Kenneth Walker III' -> 'kennethwalkeriii' would NOT
    match a pool 'Kenneth Walker' -> 'kennethwalker' (the exact bug that let an
    already-drafted player keep getting recommended). Order matters: strip the
    word tokens while spaces still exist, THEN collapse to bare alnum."""
    s = (s or "").lower()
    # drop trailing generational suffix tokens (may be more than one, e.g. "jr ii")
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)


def feed_exists() -> bool:
    return os.path.exists(_FEED)


def read_feed() -> list[dict]:
    """All picks currently in the feed file (empty list if none/unreadable)."""
    if not os.path.exists(_FEED):
        return []
    try:
        with open(_FEED, encoding="utf-8") as f:
            return (json.load(f) or {}).get("picks", [])
    except Exception:
        return []


def read_new_picks(pool, already_drafted, my_team_name: str = ""):
    """Return picks in the feed not yet in `already_drafted`, resolved to pool
    names. Each item: {name, position, mine, overall, owner, matched}.
      - name      : canonical pool name if resolved, else the raw scraped name
      - position  : from pool if resolved, else the scraped POS
      - mine       : True if the owner label matches my_team_name
      - matched   : whether the scraped name resolved to a pool player
    Ordered by overall pick number. Safe to call every poll -- it only returns
    picks whose canonical name is NOT already in `already_drafted`.
    """
    pool_by_norm = {_norm(p.name): p for p in pool}
    drafted_norm = {_norm(n) for n in already_drafted}
    mine_lc = (my_team_name or "").strip().lower()

    out = []
    for p in sorted(read_feed(), key=lambda x: x.get("overall", 0)):
        scraped = p.get("player", "")
        pos = p.get("pos", "")
        owner = p.get("owner", "")
        pl = pool_by_norm.get(_norm(scraped))
        canon = pl.name if pl else scraped
        if _norm(canon) in drafted_norm:
            continue  # already ingested
        out.append({
            "name": canon,
            "position": (pl.position if pl else pos),
            "mine": bool(mine_lc) and mine_lc in owner.lower(),
            "overall": p.get("overall"),
            "owner": owner,
            "matched": pl is not None,
        })
    return out
