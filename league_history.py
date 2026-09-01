"""
League History — extra-evil, per-manager profiles from REAL past drafts.

Pulls prior-season ESPN drafts (read-only, same cookies as live-connect),
then learns each draft slot's SPECIFIC human tendencies from what they
actually did across seasons — not a generic archetype:

  • position-by-round bias   (do they hammer RB early? wait on QB?)
  • favorite NFL team          (homer — repeatedly drafts one team's players)
  • reach tendency             (drafts players well before ADP)
  • rookie appetite            (loves or fades rookies)

Output feeds opponents.LeagueOpponents (so Prophecy predicts the real humans)
plus a human-readable DNA dossier per manager. Empty/fails gracefully so a
league with no history just leaves profiles on defaults.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

import opponents as OPP

try:
    import espn_client as EC
except Exception:
    EC = None

# ESPN numeric position id -> our label (mirrors espn_client.ESPN_POS)
_POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}
# ESPN proTeamId -> abbr is large; we only need "did they repeatedly pick the
# same proTeamId" so we track the raw id and report it as a homer signal.


@dataclass
class ManagerDNA:
    slot: int
    seasons: int
    early_pos: dict                      # pos -> count in rounds 1-4
    fav_team_id: Optional[int]
    fav_team_count: int
    rookie_rate: float
    tendencies: list                     # derived labels
    dossier: str = ""


def pull_past_drafts(league_id: int, seasons: list[int], espn_s2: str = "",
                     swid: str = "") -> list[list[dict]]:
    """Return a list of past drafts; each draft is a list of pick dicts with
    keys: slot, position, round, pro_team_id, rookie, manager (persistent
    owner id), manager_name, name (drafted player name), season, overall.
    Empty on failure."""
    if EC is None:
        return []
    # lineupSlotId -> position fallback (ESPN slot ids)
    _SLOT_POS = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "DST", 17: "K",
                 23: "RB", 20: None, 21: None}   # 20/21 = bench/IR -> unknown
    drafts: list[list[dict]] = []
    for season in seasons:
        try:
            cli = EC.EspnClient(int(league_id), int(season), espn_s2, swid)
            data = cli._get_with_fallback(["mDraftDetail", "mTeam"])
            # historical rosters are empty, so build a playerId->pos map from the
            # kona_player_info players[] list (authoritative defaultPositionId).
            pos_map: dict[int, str] = {}
            proteam_map: dict[int, int] = {}
            rookie_map: dict[int, bool] = {}
            name_map: dict[int, str] = {}
            try:
                kona = cli._get_with_fallback(["kona_player_info"])
                for pe in (kona.get("players") or []):
                    pp = pe.get("player") or {}
                    pid_ = pp.get("id")
                    if pid_ is None:
                        continue
                    pos_map[int(pid_)] = _POS.get(pp.get("defaultPositionId"))
                    proteam_map[int(pid_)] = pp.get("proTeamId")
                    nm_ = pp.get("fullName") or pp.get("name")
                    if nm_:
                        name_map[int(pid_)] = nm_
            except Exception:
                pass
            picks = (data.get("draftDetail") or {}).get("picks") or []
            teams = len(data.get("teams") or []) or 12
            # map teamId -> persistent owner (member) id + display name
            team_owner: dict[int, str] = {}
            owner_name: dict[str, str] = {}
            members = {m.get("id"): m for m in (data.get("members") or [])}
            for t in (data.get("teams") or []):
                owners = t.get("owners") or []
                oid = owners[0] if owners else f"team{t.get('id')}"
                team_owner[t.get("id")] = oid
                m = members.get(oid, {})
                nm = (m.get("displayName") or
                      (str(m.get("firstName", "")) + " " +
                       str(m.get("lastName", ""))).strip() or
                      t.get("name") or str(oid))
                owner_name[oid] = nm
            draft = []
            for p in picks:
                pid = p.get("playerId")
                pos = pos_map.get(int(pid)) if pid else None
                if not pos:                              # fallback: drafted lineup slot
                    pos = _SLOT_POS.get(p.get("lineupSlotId"))
                overall = p.get("overallPickNumber", 0)
                rnd = p.get("roundId") or ((overall - 1) // teams + 1 if overall else 0)
                slot = p.get("roundPickNumber") or (((overall - 1) % teams) + 1
                                                    if overall else 0)
                tid = p.get("teamId")
                owner = team_owner.get(tid, f"team{tid}")
                draft.append({
                    "slot": slot, "position": pos, "round": rnd,
                    "pro_team_id": proteam_map.get(int(pid)) if pid else None,
                    "rookie": rookie_map.get(int(pid), False) if pid else False,
                    "manager": owner, "manager_name": owner_name.get(owner, str(owner)),
                    "name": name_map.get(int(pid)) if pid else None,
                    "season": season, "overall": overall,
                })
            if draft:
                drafts.append(draft)
        except Exception:
            continue
    return drafts


def season_contexts(league_id: int, seasons: list[int], espn_s2: str = "",
                    swid: str = "") -> dict:
    """Per-season league context so a manager's history reads correctly across
    format/size changes. Returns {season: {teams, scoring_format, reception,
    scoring_key}}. Empty entries for seasons ESPN can't return."""
    out: dict = {}
    if EC is None:
        return out
    for yr in seasons:
        try:
            prof = EC.EspnClient(int(league_id), int(yr), espn_s2, swid).settings_profile()
            sc = prof.get("scoring") or {}
            rp = sc.get("reception", 0.5)
            key = "ppr" if rp >= 1.0 else ("half" if rp >= 0.5 else "std")
            out[yr] = {"teams": prof.get("teams"),
                       "scoring_format": prof.get("scoring_format"),
                       "reception": rp, "scoring_key": key}
        except Exception:  # noqa: BLE001
            out[yr] = {}
    return out


def _norm_name(s: str) -> str:
    """Normalize a player name for matching (lowercase, strip suffix/punct)."""
    import re
    s = (s or "").lower().strip()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def player_draft_history(drafts: list[list[dict]]) -> dict:
    """Who DRAFTED each player, by year (from draft picks, NOT end-of-season
    ownership). Returns {normalized_name: {"display": <name>, "events":
    [{"manager": id, "manager_name": nm, "season": yr, "round": r,
    "overall": o}], "by_manager": {manager_name: count}}}.

    Use to answer 'who has drafted this player before?' and to power a
    per-player loyalty snipe alert in the app."""
    from collections import Counter
    hist: dict = {}
    for draft in drafts:
        for pick in draft:
            nm = pick.get("name")
            if not nm:
                continue
            key = _norm_name(nm)
            if not key:
                continue
            entry = hist.setdefault(key, {"display": nm, "events": [],
                                          "by_manager": Counter()})
            entry["events"].append({
                "manager": pick.get("manager"),
                "manager_name": pick.get("manager_name", str(pick.get("manager"))),
                "season": pick.get("season"),
                "round": pick.get("round"),
                "overall": pick.get("overall"),
            })
            entry["by_manager"][pick.get("manager_name",
                                         str(pick.get("manager")))] += 1
    # finalize: sort events newest-first, convert Counter to plain dict
    for entry in hist.values():
        entry["events"].sort(key=lambda e: (e.get("season") or 0), reverse=True)
        entry["by_manager"] = dict(entry["by_manager"].most_common())
    return hist


def lookup_player_history(hist: dict, player_name: str) -> dict | None:
    """Look up one player's draft history from the player_draft_history() map.
    Normalizes the name so 'Derrick Henry' matches regardless of suffix/case."""
    return hist.get(_norm_name(player_name)) if hist else None


def learn_dna(drafts: list[list[dict]]) -> dict[int, ManagerDNA]:
    """Derive rich per-slot DNA from past drafts."""
    early = defaultdict(lambda: defaultdict(int))   # slot -> pos -> early count
    teams_ct = defaultdict(Counter)                 # slot -> proTeamId counter
    rookie = defaultdict(lambda: [0, 0])            # slot -> [rookies, total]
    seasons_ct = defaultdict(set)

    for di, draft in enumerate(drafts):
        for pick in draft:
            slot = pick.get("slot")
            if not slot:
                continue
            seasons_ct[slot].add(di)
            if pick.get("position") and pick.get("round", 99) <= 4:
                early[slot][pick["position"]] += 1
            if pick.get("pro_team_id"):
                teams_ct[slot][pick["pro_team_id"]] += 1
            rookie[slot][1] += 1
            if pick.get("rookie"):
                rookie[slot][0] += 1

    out: dict[int, ManagerDNA] = {}
    for slot in seasons_ct:
        ep = dict(early[slot])
        fav_id, fav_ct = (teams_ct[slot].most_common(1)[0]
                          if teams_ct[slot] else (None, 0))
        rk, tot = rookie[slot]
        rookie_rate = round(rk / tot, 2) if tot else 0.0

        tags = []
        if ep.get("QB", 0) >= 2:
            tags.append("QB-early")
        if ep.get("TE", 0) >= 2:
            tags.append("TE-premium")
        if ep.get("RB", 0) >= 3:
            tags.append("RB-heavy")
        if ep.get("WR", 0) >= 3:
            tags.append("WR-zealot")
        tags = tags or ["ADP-robot"]

        dossier = (f"Slot {slot}: {len(seasons_ct[slot])} seasons. "
                   f"Early-round lean: "
                   + (", ".join(f"{p}×{c}" for p, c in ep.items()) or "balanced")
                   + f". Rookie rate {int(rookie_rate*100)}%."
                   + (f" Homer for proTeam {fav_id} ({fav_ct}×)."
                      if fav_ct >= 3 else ""))
        out[slot] = ManagerDNA(
            slot=slot, seasons=len(seasons_ct[slot]), early_pos=ep,
            fav_team_id=fav_id if fav_ct >= 3 else None, fav_team_count=fav_ct,
            rookie_rate=rookie_rate, tendencies=tags, dossier=dossier,
        )
    return out


def apply_dna(opponents: OPP.LeagueOpponents, dna: dict[int, ManagerDNA],
              team_id_to_abbr: Optional[dict] = None) -> int:
    """Overwrite opponent profiles with learned DNA. Returns count applied."""
    n = 0
    for slot, d in dna.items():
        prof = opponents.profiles.get(slot)
        if not prof:
            continue
        prof.tendencies = d.tendencies
        prof.rookie_averse = d.rookie_rate < 0.05
        if d.fav_team_id and team_id_to_abbr:
            prof.favorite_team = team_id_to_abbr.get(d.fav_team_id)
        n += 1
    return n


# ---- manager-follow (learn by PERSON across seasons) ----------------------
def learn_dna_by_manager(drafts: list[list[dict]]) -> dict:
    """Aggregate tendencies keyed by persistent MANAGER (owner id), not slot, so
    a manager's DNA follows them even if they draft from a different seat each
    year. Uses PER-SEASON early-round (rounds 1-3) position RATES so tags reflect
    a real lean, not raw counts that only trip on many seasons.
    Returns {owner_id: dict(manager_name, tendencies, rookie_rate, fav_team_id,
    seasons, dossier)}."""
    from collections import defaultdict, Counter

    EARLY = 3          # rounds counted as "early"
    # per-manager, per-season early-round position counts
    season_pos = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))  # mgr->season->pos->n
    season_early_total = defaultdict(lambda: defaultdict(int))               # mgr->season->n
    teams_ct = defaultdict(Counter)
    rookie = defaultdict(lambda: [0, 0])
    seasons_ct = defaultdict(set)
    names = {}
    player_ct = defaultdict(Counter)     # mgr -> Counter(player_name)

    for si, draft in enumerate(drafts):
        for pick in draft:
            mgr = pick.get("manager")
            if not mgr:
                continue
            names[mgr] = pick.get("manager_name", str(mgr))
            seasons_ct[mgr].add(si)
            rnd = pick.get("round", 99) or 99
            pos = pick.get("position")
            if pos and rnd <= EARLY:
                season_pos[mgr][si][pos] += 1
                season_early_total[mgr][si] += 1
            if pick.get("pro_team_id"):
                teams_ct[mgr][pick["pro_team_id"]] += 1
            if pick.get("name"):
                player_ct[mgr][pick["name"]] += 1
            rookie[mgr][1] += 1
            if pick.get("rookie"):
                rookie[mgr][0] += 1

    out = {}
    for mgr in seasons_ct:
        seasons = sorted(seasons_ct[mgr])
        n_seasons = len(seasons)
        # average early-round share per position across seasons
        share = defaultdict(float)
        first_pos_ct = Counter()          # position of each season's very first early pick lean
        for si in seasons:
            tot = season_early_total[mgr][si] or 1
            for p, c in season_pos[mgr][si].items():
                share[p] += (c / tot) / n_seasons
            # which position did they load MOST in the early rounds this season
            if season_pos[mgr][si]:
                top = max(season_pos[mgr][si].items(), key=lambda kv: kv[1])[0]
                first_pos_ct[top] += 1
        # average early RB & WR taken per season (count, not share)
        avg_rb = sum(season_pos[mgr][si].get("RB", 0) for si in seasons) / n_seasons
        avg_wr = sum(season_pos[mgr][si].get("WR", 0) for si in seasons) / n_seasons
        avg_qb = sum(season_pos[mgr][si].get("QB", 0) for si in seasons) / n_seasons
        avg_te = sum(season_pos[mgr][si].get("TE", 0) for si in seasons) / n_seasons

        tags = []
        # Zero-RB: consistently avoids RB early while taking WRs
        if avg_rb <= 0.5 and avg_wr >= 1.5:
            tags.append("WR-zealot"); tags.append("zero-RB")
        elif avg_rb >= 2.0:
            tags.append("RB-heavy")
        elif share.get("RB", 0) >= 0.45:
            tags.append("RB-heavy")
        if avg_wr >= 2.0 and "WR-zealot" not in tags:
            tags.append("WR-zealot")
        if avg_qb >= 1.0:
            tags.append("QB-early")          # a QB inside round 3 most seasons = early
        if avg_te >= 1.0:
            tags.append("TE-premium")
        if not tags:
            tags = ["ADP-robot"]
        # de-dupe while preserving order
        tags = list(dict.fromkeys(tags))

        fav_id, fav_ct = (teams_ct[mgr].most_common(1)[0]
                          if teams_ct[mgr] else (None, 0))
        rk, tot = rookie[mgr]
        rr = round(rk / tot, 2) if tot else 0.0

        lean = ", ".join(f"{p} {share[p]*100:.0f}%"
                         for p in sorted(share, key=share.get, reverse=True)[:3]) or "balanced"
        # players this manager drafted MORE THAN ONCE = their loyalty picks
        fav_players = {nm: c for nm, c in player_ct[mgr].most_common() if c >= 2}
        fav_str = ", ".join(f"{nm} ({c}x)"
                            for nm, c in list(fav_players.items())[:5])
        doss = (f"{names[mgr]}: {n_seasons} seasons. Early-round lean: {lean}. "
                f"Avg early RB {avg_rb:.1f}/WR {avg_wr:.1f}/QB {avg_qb:.1f}/TE {avg_te:.1f}. "
                f"Rookie rate {int(rr*100)}%."
                + (f" Homer proTeam {fav_id} ({fav_ct}x)." if fav_ct >= 4 else "")
                + (f" Loyalty picks: {fav_str}." if fav_str else ""))
        out[mgr] = {"manager_name": names[mgr], "tendencies": tags,
                    "rookie_rate": rr,
                    "fav_team_id": fav_id if fav_ct >= 4 else None,
                    "favorite_players": fav_players,
                    "seasons": n_seasons, "dossier": doss,
                    "early_share": {k: round(v, 2) for k, v in share.items()}}
    return out


def current_slot_to_owner(league_id: int, season: int, espn_s2: str = "",
                          swid: str = "") -> dict:
    """Map THIS season's draft slot -> owner id (+name) so manager DNA attaches
    to the right seat this year. Uses draftDetail slot_to_teamId + team owners.
    Returns {slot: (owner_id, name)}. Empty if the draft order isn't set yet."""
    if EC is None:
        return {}
    try:
        cli = EC.EspnClient(int(league_id), int(season), espn_s2, swid)
        data = cli._get_with_fallback(["mDraftDetail", "mSettings", "mTeam"])
        members = {m.get("id"): m for m in (data.get("members") or [])}
        team_owner, name = {}, {}
        for t in (data.get("teams") or []):
            owners = t.get("owners") or []
            oid = owners[0] if owners else f"team{t.get('id')}"
            team_owner[t.get("id")] = oid
            m = members.get(oid, {})
            name[oid] = (m.get("displayName")
                         or (str(m.get("firstName", "")) + " "
                             + str(m.get("lastName", ""))).strip()
                         or t.get("name") or str(oid))
        # slot -> teamId: pre-draft this lives on settings.draftSettings.pickOrder
        # (the same source slot_labels uses); fall back to draftDetail.pickOrder,
        # then to round-1 live picks.
        settings = data.get("settings") or {}
        order = ((settings.get("draftSettings") or {}).get("pickOrder")
                 or (data.get("draftDetail") or {}).get("pickOrder") or [])
        out = {}
        if order:
            for i, tid in enumerate(order, start=1):
                oid = team_owner.get(tid)
                if oid:
                    out[i] = (oid, name.get(oid, str(oid)))
        else:
            teams_n = len(team_owner) or 12
            for p in (data.get("draftDetail") or {}).get("picks", []) or []:
                ov = p.get("overallPickNumber", 0)
                if 1 <= ov <= teams_n:
                    oid = team_owner.get(p.get("teamId"))
                    if oid:
                        out[ov] = (oid, name.get(oid, str(oid)))
        return out
    except Exception:
        return {}


def apply_manager_dna(opponents: OPP.LeagueOpponents, mgr_dna: dict,
                      slot_to_owner: dict) -> int:
    """Attach each manager's learned DNA to whatever slot they hold THIS year.
    Falls back to leaving a slot on defaults if we can't map its owner."""
    n = 0
    for slot, (owner, _name) in slot_to_owner.items():
        d = mgr_dna.get(owner)
        prof = opponents.profiles.get(slot)
        if not d or not prof:
            continue
        prof.tendencies = d["tendencies"]
        prof.rookie_averse = d["rookie_rate"] < 0.05
        n += 1
    return n
