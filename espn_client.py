"""
ESPN live-draft connector.

Reads a live/in-progress ESPN fantasy football draft using the undocumented v3
"lm-api-reads" endpoint, authenticated with the user's browser cookies
(espn_s2 + SWID). Private leagues REQUIRE both cookies; public leagues work
without them.

Endpoint (confirmed via ffverse/ffscrapr + community wikis, 2026):
  https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}
      /segments/0/leagues/{league_id}?view=mDraftDetail&view=mSettings&view=mTeam

Older host fallback (some accounts/leagues still resolve here):
  https://fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}

Poll the draftDetail 'picks' array during the draft to see who's gone and whose
turn it is. ESPN returns picks with numeric ESPN playerId + overallPickNumber +
teamId + roundId; we normalize to the engine's pick shape and resolve player
names/positions from the roster/players view (or the shared player map).

Draft is read-only. Be gentle: poll every ~4-5s, well within tolerance.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

try:
    import requests
except ImportError:  # requests ships with the LRDP 3.13 env; guard for safety
    requests = None  # type: ignore

READS_HOST = "https://lm-api-reads.fantasy.espn.com"
LEGACY_HOST = "https://fantasy.espn.com"
FAN_HOST = "https://fan.api.espn.com"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FantasyDraftAssistant/1.0"

# ESPN encodes position as a numeric slot id on the player object.
ESPN_POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}


def _norm_name(name: str) -> str:
    """Normalize a player name for cross-source matching (strip Jr./III/punct)."""
    import re as _re
    n = (name or "").lower()
    for suf in (" jr.", " jr", " sr.", " sr", " iii", " ii", " iv", " v"):
        if n.endswith(suf):
            n = n[: -len(suf)]
    return _re.sub(r"[^a-z0-9]", "", n)


@dataclass
class EspnPick:
    overall: int
    round: int
    team_id: int
    player_id: int          # ESPN numeric id
    player_name: str = ""
    position: str = ""
    pro_team: str = ""


@dataclass
class EspnDraftState:
    in_progress: bool
    complete: bool
    picks: list[EspnPick]
    teams: int
    total_rounds: int
    on_the_clock_team: Optional[int] = None
    drafted_player_ids: set[int] = field(default_factory=set)


class EspnClient:
    def __init__(self, league_id: int, season: int, espn_s2: str = "", swid: str = "",
                 timeout: float = 8.0):
        if requests is None:
            raise RuntimeError("the `requests` package is required for ESPN live-connect")
        self.league_id = int(league_id)
        self.season = int(season)
        self.timeout = timeout
        self.sess = requests.Session()
        self.sess.headers.update({"User-Agent": _UA, "Accept": "application/json"})
        # SWID must be wrapped in {braces}; tolerate the user pasting it either way.
        if swid and not swid.startswith("{"):
            swid = "{" + swid.strip("{}") + "}"
        if espn_s2 and swid:
            self.sess.cookies.update({"espn_s2": espn_s2, "SWID": swid})
        self._player_cache: dict[int, dict] = {}

    # ---- low level ----
    def _get(self, host: str, views: list[str]) -> dict:
        url = (f"{host}/apis/v3/games/ffl/seasons/{self.season}"
               f"/segments/0/leagues/{self.league_id}")
        params = [("view", v) for v in views]
        r = self.sess.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _get_with_fallback(self, views: list[str]) -> dict:
        try:
            return self._get(READS_HOST, views)
        except Exception:
            return self._get(LEGACY_HOST, views)

    def verify(self) -> tuple[bool, str]:
        """Cheap auth/connectivity check. Returns (ok, message)."""
        try:
            data = self._get_with_fallback(["mSettings"])
            name = (data.get("settings") or {}).get("name", "your league")
            return True, f"Connected to '{name}' (league {self.league_id}, {self.season})."
        except Exception as ex:  # noqa: BLE001
            msg = str(ex)
            if "401" in msg or "403" in msg:
                return False, ("ESPN rejected the request (401/403). For a PRIVATE "
                               "league you must supply BOTH espn_s2 and SWID cookies, "
                               "and they must be current.")
            if "404" in msg:
                return False, "League not found — check the league_id and season."
            return False, f"Could not reach ESPN: {msg}"

    def league_profile(self) -> dict:
        """League name + season + every team (id, name, owner GUID), with MY
        team flagged by matching the SWID owner GUID. Used to preload/identify
        saved leagues and to know my team name for weekly lineup decisions."""
        data = self._get_with_fallback(["mSettings", "mTeam"])
        settings = data.get("settings") or {}
        league_name = settings.get("name") or f"League {self.league_id}"

        # my owner GUID = the SWID cookie (uppercased, braces normalized)
        my_guid = ""
        swid = self.sess.cookies.get("SWID") or ""
        if swid:
            my_guid = "{" + swid.strip("{}").upper() + "}"

        # members: GUID -> display name (for a friendlier "mine" label)
        members = {}
        for m in data.get("members", []) or []:
            gid = (m.get("id") or "").upper()
            if not gid.startswith("{"):
                gid = "{" + gid.strip("{}") + "}"
            nm = (f'{m.get("firstName","")} {m.get("lastName","")}'.strip()
                  or m.get("displayName", ""))
            members[gid] = nm

        teams = []
        my_team_name = ""
        my_team_id = None
        for t in data.get("teams", []) or []:
            # team display name: ESPN 2024+ uses 'name'; older uses location+nickname
            tname = (t.get("name")
                     or f'{t.get("location","")} {t.get("nickname","")}'.strip()
                     or f'Team {t.get("id")}')
            owners = [o.upper() if isinstance(o, str) else o
                      for o in (t.get("owners") or [])]
            owners = ["{" + o.strip("{}") + "}" if isinstance(o, str)
                      and not o.startswith("{") else o for o in owners]
            is_mine = bool(my_guid) and my_guid in owners
            if is_mine:
                my_team_name = tname
                my_team_id = t.get("id")
            teams.append({"id": t.get("id"), "name": tname,
                          "owner": (members.get(owners[0]) if owners else ""),
                          "is_mine": is_mine})

        return {
            "league_id": self.league_id,
            "season": self.season,
            "league_name": league_name,
            "teams": teams,
            "my_team_name": my_team_name,
            "my_team_id": my_team_id,
            "team_count": len(teams),
        }

    def slot_labels(self) -> dict:
        """Map DRAFT SLOT (1-indexed) -> a display label 'Team Name — Owner' for
        every team, so the board can show real names instead of 'slot N'.

        Slot order comes from draftSettings.pickOrder (the array of teamIds in
        first-round draft order) when present; otherwise from live draft picks
        (round-1 overall pick -> teamId); otherwise falls back to team-id order.
        Returns {slot:int -> {'team': str, 'owner': str, 'team_id': int,
        'label': str, 'is_mine': bool}}.
        """
        data = self._get_with_fallback(["mSettings", "mTeam", "mDraftDetail"])
        settings = data.get("settings") or {}
        draft_settings = settings.get("draftSettings") or {}

        my_guid = ""
        swid = self.sess.cookies.get("SWID") or ""
        if swid:
            my_guid = "{" + swid.strip("{}").upper() + "}"

        # members GUID -> owner display name
        members = {}
        for m in data.get("members", []) or []:
            gid = (m.get("id") or "").upper()
            if not gid.startswith("{"):
                gid = "{" + gid.strip("{}") + "}"
            members[gid] = (f'{m.get("firstName","")} {m.get("lastName","")}'.strip()
                            or m.get("displayName", ""))

        # teamId -> (team name, owner name, is_mine)
        team_info = {}
        for t in data.get("teams", []) or []:
            tid = t.get("id")
            tname = (t.get("name")
                     or f'{t.get("location","")} {t.get("nickname","")}'.strip()
                     or f'Team {tid}')
            owners = t.get("owners") or []
            g0 = ""
            if owners and isinstance(owners[0], str):
                g0 = owners[0].upper()
                if not g0.startswith("{"):
                    g0 = "{" + g0.strip("{}") + "}"
            owner_name = members.get(g0, "")
            is_mine = bool(my_guid) and g0 == my_guid
            team_info[tid] = {"team": tname, "owner": owner_name, "is_mine": is_mine}

        # slot -> teamId
        slot_to_team = {}
        pick_order = draft_settings.get("pickOrder") or []
        if pick_order:                                   # pre-draft authoritative
            for i, tid in enumerate(pick_order, start=1):
                slot_to_team[i] = tid
        else:                                            # derive from round-1 picks
            teams_n = len(team_info) or 12
            for p in (data.get("draftDetail") or {}).get("picks", []) or []:
                ov = p.get("overallPickNumber", 0)
                if 1 <= ov <= teams_n:                   # round 1 = slot order
                    slot_to_team[ov] = p.get("teamId")
        if not slot_to_team:                             # last resort: id order
            for i, tid in enumerate(sorted(team_info), start=1):
                slot_to_team[i] = tid

        out = {}
        for slot, tid in slot_to_team.items():
            info = team_info.get(tid, {"team": f"Team {tid}", "owner": "",
                                       "is_mine": False})
            label = info["team"]
            if info["owner"]:
                label += f' — {info["owner"]}'
            out[slot] = {"team_id": tid, "team": info["team"],
                         "owner": info["owner"], "is_mine": info["is_mine"],
                         "label": label}
        return out

    def settings_profile(self) -> dict:
        """Extract the league's real config for the engine: team count, per-stat
        scoring point values (mapped to our Scoring knobs), and lineup slot
        counts (mapped to our starters dict). Empty dict on failure.

        ESPN statIds we map: 53=receptions, 3=passYd, 4=passTD, 20=INT,
        24=rushYd, 25=rushTD, 42=recYd, 43=recTD, 72=fumbleLost.
        Lineup slotIds: 0=QB 2=RB 4=WR 6=TE 23=FLEX 16=DST 17=K 20=BENCH 21=IR."""
        data = self._get_with_fallback(["mSettings"])
        settings = data.get("settings") or {}

        teams = settings.get("size") or (len(data.get("teams") or []) or 12)

        # ---- scoring: statId -> points ----
        sc = settings.get("scoringSettings") or {}
        items = {int(it.get("statId")): float(it.get("points", 0))
                 for it in (sc.get("scoringItems") or []) if it.get("statId") is not None}
        scoring = {
            "reception": items.get(53, 0.5),
            "pass_yd": items.get(3, 0.04),
            "pass_td": items.get(4, 4.0),
            "interception": items.get(20, -2.0),
            "rush_yd": items.get(24, 0.1),
            "rush_td": items.get(25, 6.0),
            "rec_yd": items.get(42, 0.1),
            "rec_td": items.get(43, 6.0),
            "fumble_lost": items.get(72, -2.0),
            # Kicker (ESPN statIds): 77=FG0-39, 78=FG40-49, 79=FG50+,
            # 80=FG made(any dist fallback), 85=FG missed, 86=XP made, 88=XP missed
            "fg_0_39": items.get(77, items.get(80, 3.0)),
            "fg_40_49": items.get(78, 4.0),
            "fg_50": items.get(79, 5.0),
            "fg_miss": items.get(85, -1.0),
            "xp_made": items.get(86, 1.0),
            "xp_miss": items.get(88, -1.0),
            # D/ST: 99=sack 95=INT 96=fumRec 98=safety 97=block; TDs 93/103/104/105/106
            "dst_sack": items.get(99, 1.0),
            "dst_int": items.get(95, 2.0),
            "dst_fum_rec": items.get(96, 2.0),
            "dst_td": items.get(93, items.get(103, 6.0)),
            "dst_safety": items.get(98, 2.0),
            "dst_block": items.get(97, 2.0),
            # points-allowed tiers: 89=0 90=1-6 91=7-13 92=14-17 93pa.. ESPN
            # uses 89-95 for PA tiers; keep league values when present.
            "dst_pa_0": items.get(89, 5.0),
            "dst_pa_1_6": items.get(90, 4.0),
            "dst_pa_7_13": items.get(91, 3.0),
            "dst_pa_14_17": items.get(92, 1.0),
            "dst_pa_18_27": items.get(94, 0.0),
            "dst_pa_28_34": items.get(123, -1.0),
            "dst_pa_35": items.get(124, -4.0),
        }
        # human label for the reception format
        rp = scoring["reception"]
        fmt = "PPR" if rp >= 1.0 else ("Half PPR" if rp >= 0.5 else "Standard")

        # ---- lineup slots -> our starters dict ----
        rs = settings.get("rosterSettings") or {}
        counts = {int(k): int(v) for k, v in (rs.get("lineupSlotCounts") or {}).items()}
        _SLOT = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 23: "FLEX",
                 16: "DST", 17: "K"}
        starters = {}
        for sid, pos in _SLOT.items():
            n = counts.get(sid, 0)
            if n:
                starters[pos] = starters.get(pos, 0) + n
        # superflex (slot 7 = OP/QB-flex) if present
        if counts.get(7):
            starters["SUPERFLEX"] = counts[7]
        bench = counts.get(20, 0)

        return {
            "teams": int(teams),
            "scoring": scoring,
            "scoring_format": fmt,
            "starters": starters or None,
            "bench": bench or None,
            "league_name": settings.get("name", ""),
        }

    def rostered_players(self) -> dict:
        """Return {'names': set[str], 'by_team': {team_name: [names]}} for every
        player currently on a roster in the league. Free agents = pool − names.
        Uses mRoster + mTeam."""
        data = self._get_with_fallback(["mRoster", "mTeam", "mSettings"])
        self._ensure_players(data)
        names: set[str] = set()
        by_team: dict[str, list[str]] = {}
        for t in data.get("teams", []) or []:
            tname = (t.get("name")
                     or f'{t.get("location","")} {t.get("nickname","")}'.strip()
                     or f'Team {t.get("id")}')
            roster = (t.get("roster") or {}).get("entries") or []
            for ent in roster:
                pp = (ent.get("playerPoolEntry") or {}).get("player") or {}
                nm = pp.get("fullName")
                if nm:
                    names.add(nm)
                    by_team.setdefault(tname, []).append(nm)
        return {"names": names, "by_team": by_team}

    def player_week_points(self, week: int) -> dict:
        """Return {normalized_name: fantasy_points} for a given scoring week,
        using this league's scoring settings. Pulls kona_player_info with the
        scoringPeriodId filter; reads each player's stats[] entry whose
        scoringPeriodId==week and statSourceId==0 (actual, not projected) and
        takes its appliedTotal. Empty on failure."""
        url = (f"{READS_HOST}/apis/v3/games/ffl/seasons/{self.season}"
               f"/segments/0/leagues/{self.league_id}")
        headers = {
            "x-fantasy-filter": json.dumps({
                "players": {"limit": 700,
                            "filterStatsForCurrentSeasonScoringPeriodId":
                                {"value": [int(week)]}}}),
        }
        out: dict[str, float] = {}
        try:
            r = self.sess.get(url, params=[("view", "kona_player_info"),
                                           ("scoringPeriodId", int(week))],
                              headers=headers, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except Exception:
            try:
                data = self._get(LEGACY_HOST, ["kona_player_info"])
            except Exception:
                return {}
        for pe in (data.get("players") or []):
            pp = pe.get("player") or {}
            nm = pp.get("fullName")
            if not nm:
                continue
            pts = None
            for stat in (pp.get("stats") or []):
                if stat.get("scoringPeriodId") == int(week) and \
                   stat.get("statSourceId") == 0:      # 0 = actual
                    pts = stat.get("appliedTotal")
                    break
            if pts is not None:
                out[_norm_name(nm)] = float(pts)
        return out

    # ---- player id -> name/pos resolution ----
    def _ensure_players(self, data: dict) -> None:
        """Cache player metadata from any view that carries roster player entries."""
        for team in data.get("teams", []) or []:
            entries = ((team.get("roster") or {}).get("entries")) or []
            for ent in entries:
                pp = (ent.get("playerPoolEntry") or {}).get("player") or {}
                pid = pp.get("id")
                if pid is not None:
                    self._player_cache[int(pid)] = pp

    def _resolve(self, pid: int) -> tuple[str, str, str]:
        pp = self._player_cache.get(int(pid))
        if not pp:
            return "", "", ""
        name = pp.get("fullName", "")
        pos = ESPN_POS.get(pp.get("defaultPositionId"), "")
        pro = str(pp.get("proTeamId", ""))
        return name, pos, pro

    # ---- draft state ----
    def draft_state(self) -> EspnDraftState:
        data = self._get_with_fallback(["mDraftDetail", "mSettings", "mTeam"])
        self._ensure_players(data)

        settings = data.get("settings") or {}
        draft_settings = settings.get("draftSettings") or {}
        # ESPN sizes: number of teams + roster/lineup drives rounds
        teams = len((data.get("teams") or [])) or draft_settings.get("orderCount", 0) or 12

        dd = data.get("draftDetail") or {}
        in_progress = bool(dd.get("inProgress"))
        drafted = dd.get("drafted")
        raw_picks = dd.get("picks") or []

        picks: list[EspnPick] = []
        drafted_ids: set[int] = set()
        for p in raw_picks:
            pid = p.get("playerId")
            if pid is None or int(pid) <= 0:
                continue  # unfilled slot
            name, pos, pro = self._resolve(int(pid))
            picks.append(EspnPick(
                overall=p.get("overallPickNumber", 0),
                round=p.get("roundId", 0),
                team_id=p.get("teamId", 0),
                player_id=int(pid),
                player_name=name, position=pos, pro_team=pro,
            ))
            drafted_ids.add(int(pid))

        total_rounds = 0
        if picks and teams:
            total_rounds = max(pk.round for pk in picks)
        complete = bool(drafted) and not in_progress

        # whose turn: the team owning the next unfilled overall pick (snake math)
        on_clock = None
        if in_progress and teams:
            next_overall = len(picks) + 1
            on_clock = _snake_team_for_overall(next_overall, teams)

        return EspnDraftState(
            in_progress=in_progress, complete=complete, picks=picks,
            teams=teams, total_rounds=total_rounds,
            on_the_clock_team=on_clock, drafted_player_ids=drafted_ids,
        )

    def poll(self, on_state, interval: float = 4.5, stop=lambda: False) -> None:
        """Blocking poll loop. `on_state(EspnDraftState)` fires each tick until
        the draft completes or `stop()` returns True. Streamlit uses draft_state()
        directly on a timer instead of this loop."""
        while not stop():
            st = self.draft_state()
            on_state(st)
            if st.complete:
                return
            time.sleep(interval)


def _snake_team_for_overall(overall: int, teams: int) -> int:
    """1-indexed team/slot that owns a given overall pick number in a snake."""
    rnd = (overall - 1) // teams + 1
    idx_in_round = (overall - 1) % teams  # 0-indexed
    if rnd % 2 == 1:
        return idx_in_round + 1
    return teams - idx_in_round



def discover_leagues(espn_s2: str, swid: str, timeout: float = 10.0) -> dict:
    """Auto-discover EVERY fantasy football league on the account, using ONLY
    the two cookies — no manual league IDs.

    Hits ESPN's fan profile API:
        https://fan.api.espn.com/apis/v2/fans/{SWID}
            ?configuration=SITE_EDITION&displayEvents=true&displayNow=true
            &displayRecs=false&featureFlags=...&source=ESPN.com
    which returns a `preferences` array; each fantasy entry carries a
    `metaData.entry` with the league (groups[]) + team info.

    Returns {"ok": bool, "message": str, "leagues": [ {league_id, season,
    league_name, my_team_name, my_team_id} ... ]}.
    Football only (gameId 'ffl' / abbrev 'FFL').
    """
    if requests is None:
        return {"ok": False, "message": "requests package missing", "leagues": []}
    if not swid:
        return {"ok": False, "message": "SWID cookie is required to auto-discover "
                "leagues.", "leagues": []}
    guid = swid.strip()
    if not guid.startswith("{"):
        guid = "{" + guid.strip("{}") + "}"

    sess = requests.Session()
    sess.headers.update({"User-Agent": _UA, "Accept": "application/json"})
    if espn_s2:
        sess.cookies.update({"espn_s2": espn_s2, "SWID": guid})

    url = f"{FAN_HOST}/apis/v2/fans/{guid}"
    params = {
        "configuration": "SITE_EDITION",
        "displayEvents": "true", "displayNow": "true", "displayRecs": "false",
        "featureFlags": "fanApiFeatureUnverifiedFavorites",
        "source": "ESPN.com",
    }
    try:
        r = sess.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as ex:  # noqa: BLE001
        msg = str(ex)
        if "401" in msg or "403" in msg:
            return {"ok": False, "leagues": [],
                    "message": "ESPN rejected the request (401/403). Paste a "
                    "current espn_s2 AND SWID from a logged-in ESPN session."}
        return {"ok": False, "leagues": [], "message": f"fan API error: {msg}"}

    leagues: list[dict] = []
    seen: set[tuple] = set()
    for pref in (data.get("preferences") or []):
        meta = pref.get("metaData") or {}
        entry = meta.get("entry") or {}
        # football only
        game = str(entry.get("gameId") or pref.get("id") or "").lower()
        abbrev = (entry.get("abbrev") or "").upper()
        if "ffl" not in str(game).lower() and abbrev != "FFL" \
           and (entry.get("gameId") not in (1,)):
            # some payloads tag football with numeric gameId 1
            if abbrev not in ("FFL",):
                # be permissive: only skip if clearly a non-football abbrev
                if abbrev in ("FBA", "FLB", "FHL"):
                    continue
        groups = entry.get("groups") or []
        season = entry.get("seasonId") or entry.get("scoringPeriodId") or 0
        my_team_name = (f'{entry.get("entryLocation","")} '
                        f'{entry.get("entryNickname","")}').strip() \
            or entry.get("name") or ""
        my_team_id = entry.get("entryId") or entry.get("teamId")
        for g in groups:
            lid = g.get("groupId") or g.get("id")
            if lid is None:
                continue
            key = (int(lid), int(season) if season else 0)
            if key in seen:
                continue
            seen.add(key)
            leagues.append({
                "league_id": int(lid),
                "season": int(season) if season else 0,
                "league_name": g.get("groupName") or g.get("name") or f"League {lid}",
                "my_team_name": my_team_name,
                "my_team_id": my_team_id,
            })

    if not leagues:
        return {"ok": True, "leagues": [],
                "message": "Connected, but no fantasy football leagues were found "
                "on this account for the current view."}
    return {"ok": True, "leagues": leagues,
            "message": f"Found {len(leagues)} football league(s) on your account."}
