"""
Saved leagues — preload the ESPN league IDs you play in, resolve each league's
NAME and YOUR TEAM NAME once, then pick one from a dropdown when its draft goes
live (or when setting a weekly lineup).

Persistence: a plain JSON file next to the app (data/leagues.json). We store only
non-secret metadata — league_id, season, resolved league_name, my_team_name,
team list. Cookies (espn_s2 / SWID) are NEVER written here; they live in session
state only, exactly as before, so nothing sensitive touches disk.

Shape (list of entries):
  {
    "league_id": 123456, "season": 2026,
    "league_name": "The Money League", "my_team_name": "Project Shredder",
    "my_team_id": 7, "team_count": 12,
    "teams": [{"id":1,"name":"...","owner":"...","is_mine":false}, ...],
    "resolved": true            # false until we've fetched names from ESPN
  }
"""
from __future__ import annotations

import json
import os
from typing import Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_STORE = os.path.join(_DATA_DIR, "leagues.json")


def _ensure_dir() -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)


def load() -> list[dict]:
    if not os.path.exists(_STORE):
        return []
    try:
        with open(_STORE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save(entries: list[dict]) -> None:
    _ensure_dir()
    with open(_STORE, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def _key(league_id, season) -> tuple[int, int]:
    return int(league_id), int(season)


def add(league_id, season, label: str = "") -> list[dict]:
    """Add a league id (unresolved) if not already present. `label` is an
    optional user nickname shown until we resolve the real ESPN name."""
    entries = load()
    k = _key(league_id, season)
    for e in entries:
        if _key(e.get("league_id"), e.get("season", season)) == k:
            if label:
                e["label"] = label
            save(entries)
            return entries
    entries.append({
        "league_id": int(league_id), "season": int(season),
        "label": label or f"League {league_id}",
        "league_name": "", "my_team_name": "", "my_team_id": None,
        "team_count": 0, "teams": [], "resolved": False,
    })
    save(entries)
    return entries


def remove(league_id, season) -> list[dict]:
    entries = [e for e in load()
               if _key(e.get("league_id"), e.get("season", season)) != _key(league_id, season)]
    save(entries)
    return entries


def update_resolved(profile: dict) -> list[dict]:
    """Merge a fetched EspnClient.league_profile() result into the store."""
    entries = load()
    k = _key(profile["league_id"], profile["season"])
    found = False
    for e in entries:
        if _key(e.get("league_id"), e.get("season")) == k:
            e.update({
                "league_name": profile.get("league_name", ""),
                "my_team_name": profile.get("my_team_name", ""),
                "my_team_id": profile.get("my_team_id"),
                "team_count": profile.get("team_count", 0),
                "teams": profile.get("teams", []),
                "resolved": True,
            })
            found = True
            break
    if not found:
        entries.append({
            "league_id": profile["league_id"], "season": profile["season"],
            "label": profile.get("league_name", ""),
            "league_name": profile.get("league_name", ""),
            "my_team_name": profile.get("my_team_name", ""),
            "my_team_id": profile.get("my_team_id"),
            "team_count": profile.get("team_count", 0),
            "teams": profile.get("teams", []),
            "resolved": True,
        })
    save(entries)
    return entries


def bulk_upsert(discovered: list[dict], default_season: int = 2026) -> list[dict]:
    """Merge auto-discovered leagues (from EspnClient.discover_leagues) into the
    store. Marks each as resolved since discovery already carries name + team."""
    entries = load()
    index = {_key(e.get("league_id"), e.get("season", default_season)): e
             for e in entries}
    for d in discovered:
        season = int(d.get("season") or default_season)
        k = _key(d["league_id"], season)
        rec = index.get(k)
        payload = {
            "league_id": int(d["league_id"]), "season": season,
            "label": d.get("league_name", ""),
            "league_name": d.get("league_name", ""),
            "my_team_name": d.get("my_team_name", ""),
            "my_team_id": d.get("my_team_id"),
            "team_count": d.get("team_count", 0),
            "teams": d.get("teams", []),
            "resolved": bool(d.get("league_name")),
        }
        if rec:
            # keep any richer already-resolved detail; fill blanks from discovery
            for kk, vv in payload.items():
                if vv and not rec.get(kk):
                    rec[kk] = vv
            if payload["league_name"]:
                rec["league_name"] = payload["league_name"]
                rec["resolved"] = True
        else:
            entries.append(payload)
            index[k] = payload
    save(entries)
    return entries


def get(league_id, season) -> Optional[dict]:
    for e in load():
        if _key(e.get("league_id"), e.get("season", season)) == _key(league_id, season):
            return e
    return None


def display_label(entry: dict) -> str:
    """Human dropdown label: 'League Name — My Team (12-team) [id]'."""
    if entry.get("resolved") and entry.get("league_name"):
        team = f' — {entry["my_team_name"]}' if entry.get("my_team_name") else ""
        size = f' ({entry["team_count"]}-team)' if entry.get("team_count") else ""
        return f'{entry["league_name"]}{team}{size}  ·  id {entry["league_id"]}'
    return f'{entry.get("label") or "League"}  ·  id {entry.get("league_id")} (unresolved)'
