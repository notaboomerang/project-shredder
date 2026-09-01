"""
Project Shredder -- REST backfill for the live-sync connector.

THE PROBLEM THIS SOLVES (the last "keeps up with picks" gap)
  The DOM poller reads ESPN's rolling pick ticker (li.pick-message__container),
  which only shows the most RECENT picks. If you connect Shredder *mid-draft*
  (round 5, say), rounds 1-4 never scroll past the ticker, so the board starts
  incomplete and the engine can recommend already-drafted players. The full
  board-history DOM selector was probed against a live room and confirmed dead
  (all candidates returned 0 -- see data/dom_probe.json), so scraping the whole
  board is not viable.

THE FIX (a fundamentally different source, not another CSS guess)
  ESPN's REST mDraftDetail view *does* backfill every COMPLETED pick reliably.
  Its only limitation is the CURRENTLY-on-the-clock slot shows playerId <= 0 --
  which espn_client.draft_state() already skips. So one REST pull on connect
  gives us every pick made so far, with the TRUE overall number, round, teamId,
  and resolved player name/position. We convert those into the exact same line
  format the DOM poller writes, so both sources feed one deduped pick list.

  REST seeds the past; the live ticker handles everything going forward. The
  app-side reader dedups by normalized player name, so a pick seen by both
  sources is recorded once.

league_id + team come from the draft-room URL (leagueId=... & teamId=...) with a
fallback to the saved-leagues store. Cookies come from data/espn_cookies.json,
the same store the poller and app already use.
"""
from __future__ import annotations

import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))


def league_id_from_url(url: str) -> int | None:
    """Pull the numeric leagueId from an ESPN draft-room URL. Handles both
    ?leagueId=123 and /leagues/123 shapes."""
    if not url:
        return None
    m = re.search(r"[?&]leagueId=(\d+)", url) or re.search(r"/leagues?/(\d+)", url)
    return int(m.group(1)) if m else None


def team_id_from_url(url: str) -> int | None:
    m = re.search(r"[?&]teamId=(\d+)", url) if url else None
    return int(m.group(1)) if m else None


def _load_cookies() -> tuple[str, str]:
    path = os.path.join(_HERE, "data", "espn_cookies.json")
    s2 = swid = ""
    if os.path.exists(path):
        try:
            with open(path) as f:
                j = json.load(f)
            s2 = j.get("espn_s2", "") or j.get("s2", "")
            swid = j.get("swid", "") or j.get("SWID", "")
        except Exception:
            pass
    if swid and not swid.startswith("{"):
        swid = "{" + swid.strip("{}") + "}"
    return s2, swid


def _saved_league_ids() -> list[int]:
    """Fallback league ids from the saved-leagues store, if the URL had none."""
    try:
        import saved_leagues as _SL
        return [int(e["league_id"]) for e in _SL.load() if e.get("league_id")]
    except Exception:
        return []


def _team_name_map(client, league_id: int, season: int) -> dict[int, str]:
    """team_id -> team name, so backfilled picks carry a human owner label that
    the reader can match against the user's team name for the 'mine' flag."""
    try:
        prof = client.league_profile()
        out = {}
        for t in prof.get("teams", []) or []:
            tid = t.get("id")
            nm = t.get("name") or ""
            if tid is not None and nm:
                out[int(tid)] = nm
        return out
    except Exception:
        return {}


def backfill_picks(url: str = "", league_id: int | None = None,
                   season: int = 2026) -> list[dict]:
    """Return every COMPLETED pick from ESPN's REST draftDetail as poller-shaped
    dicts: {player, team, pos, round, slot, overall, owner, raw, _src:'rest'}.

    `overall` is the TRUE ESPN overall pick number. The app dedups by name, so
    the key only needs to preserve ordering, which real overalls do.

    LEAGUE RESOLUTION (deliberately strict):
      - If the URL carries a leagueId, use ONLY that league. We do NOT fall back
        to other saved leagues when it isn't live yet -- injecting a DIFFERENT
        draft's players into the board would be a wrong-data bug. An empty result
        just means "no picks yet", and the live ticker takes over.
      - Only when the URL has NO leagueId at all (e.g. a bare practice-room URL)
        do we try the saved-leagues list as a best-effort guess.

    Returns [] on any failure -- the poller then relies on the live ticker alone,
    exactly as before.
    """
    try:
        from espn_client import EspnClient, READS_HOST, LEGACY_HOST, ESPN_POS
    except Exception:
        return []

    url_lid = league_id or league_id_from_url(url)
    if url_lid:
        candidates = [url_lid]          # trust the URL only; no cross-league guess
    else:
        candidates = _saved_league_ids()  # bare URL -> best-effort saved leagues
    if not candidates:
        return []

    s2, swid = _load_cookies()

    for cand in candidates:
        try:
            client = EspnClient(league_id=cand, season=season,
                                espn_s2=s2, swid=swid)
        except Exception:
            continue

        # Raw draftDetail (we need the playerIds; draft_state() drops unresolved
        # ones, and completed drafts don't populate team rosters so its name
        # resolver comes back empty -- we resolve by id via kona_player_info).
        try:
            data = client._get_with_fallback(["mDraftDetail", "mSettings", "mTeam"])
        except Exception:
            continue
        raw_picks = ((data.get("draftDetail") or {}).get("picks")) or []
        completed = [p for p in raw_picks
                     if p.get("playerId") is not None and int(p["playerId"]) > 0]
        if not completed:
            continue  # league resolved but no picks yet -> caller uses ticker

        # id -> (name, pos, proTeamId) via a single id-filtered kona_player_info
        want_ids = sorted({int(p["playerId"]) for p in completed})
        id_map = _resolve_player_ids(client, READS_HOST, LEGACY_HOST, ESPN_POS,
                                     want_ids, season, cand)
        names = _team_name_map(client, cand, season)

        rows: list[dict] = []
        for p in completed:
            pid = int(p["playerId"])
            nm, pos, pro = id_map.get(pid, ("", "", ""))
            if not nm:
                continue  # can't name it -> skip rather than emit a blank pick
            overall = p.get("overallPickNumber", 0)
            rnd = p.get("roundId", 0)
            owner = names.get(p.get("teamId"), f"Team {p.get('teamId')}")
            raw = f"{nm} / {pro} {pos}   R{rnd}, P{overall} - {owner}"
            rows.append({
                "player": nm, "team": pro, "pos": pos,
                "round": rnd, "slot": overall, "overall": overall,
                "owner": owner, "raw": raw, "_src": "rest",
            })
        if rows:
            return rows
    return []


def _resolve_player_ids(client, reads_host, legacy_host, espn_pos,
                        ids: list[int], season: int, league_id: int) -> dict:
    """id -> (fullName, POS, proTeamId-as-str) via kona_player_info, id-filtered.
    Completed-draft rosters are empty, so this is the reliable name source."""
    if not ids:
        return {}
    import json as _json
    url = (f"{reads_host}/apis/v3/games/ffl/seasons/{season}"
           f"/segments/0/leagues/{league_id}")
    hdr = {"x-fantasy-filter": _json.dumps({"players": {"filterIds": {"value": ids},
                                                        "limit": len(ids) + 10}})}
    data = None
    try:
        r = client.sess.get(url, params=[("view", "kona_player_info")],
                            headers=hdr, timeout=getattr(client, "timeout", 15))
        r.raise_for_status()
        data = r.json()
    except Exception:
        try:
            data = client._get(legacy_host, ["kona_player_info"])
        except Exception:
            return {}
    out: dict[int, tuple[str, str, str]] = {}
    for pe in (data.get("players") or []):
        pp = pe.get("player") or {}
        pid = pp.get("id")
        if pid is None:
            continue
        out[int(pid)] = (pp.get("fullName", ""),
                         espn_pos.get(pp.get("defaultPositionId"), ""),
                         str(pp.get("proTeamId", "")))
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="")
    ap.add_argument("--league", type=int)
    ap.add_argument("--season", type=int, default=2026)
    a = ap.parse_args()
    picks = backfill_picks(url=a.url, league_id=a.league, season=a.season)
    print(f"backfilled {len(picks)} completed picks")
    for p in picks[:12]:
        print(f"  P{p['overall']:>3} R{p['round']} {p['player']} ({p['pos']}) -> {p['owner']}")
