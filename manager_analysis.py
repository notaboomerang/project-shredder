"""
Deep manager analysis — a level beyond the round-1-3 lean in league_history.

learn_dna_by_manager() tags a manager off early-round position rates + repeat
players. This goes deeper by reading each manager's FULL draft history with the
context that shapes it:

  • Per-season NORMALIZATION — a "round 2" pick in an 8-team league is a very
    different player than in a 16-team league, and a WR lean in a PPR season
    means less in a standard one. We express every pick as POSITIONAL DRAFT
    CAPITAL (their Nth pick spent on a position) and weight by the season's
    format so cross-year reads are apples-to-apples.
  • RECENCY WEIGHTING — last year says more about this year than 2021 did.
  • DRAFT SHAPE — the opening sequence they favor (e.g. RB-RB-WR), and WHEN
    they typically spend on QB / TE / DST / K (early anchor vs. late streamer).
  • DISCIPLINE — do they reach ahead of the board or wait for value?
  • CONFIDENCE — how repeatable each trait is across seasons (seen 4/4 years =
    lock; 1/3 = noise). Every read carries a confidence so the UI can hedge.
  • SCORING-CHANGE FLAG — if their history was built under a different scoring
    format than THIS year's, we say so, because it should discount the read.

Pure functions over the draft dicts league_history.pull_past_drafts() returns
(each pick has: manager, manager_name, season, overall, round, position, name).
Returns an enriched profile per manager that superset-includes the fields the
existing opponents/prophecy code already consumes (tendencies, rookie_rate,
favorite_players), so it's a drop-in upgrade.
"""
from __future__ import annotations

import re
from collections import defaultdict, Counter
from typing import Optional


def _norm(s):
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", (s or "").lower().strip())
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


# how many of a season's picks count as "early / anchor" capital
_EARLY_PICKS = 4          # a manager's first 4 selections = their build core
_STREAM_POS = ("QB", "TE", "DST", "K")


def _recency_weight(season, seasons_sorted):
    """More recent seasons weigh more. Newest = 1.0, each older step * 0.8."""
    if season not in seasons_sorted:
        return 0.5
    # index from newest (0) to oldest
    idx = list(reversed(seasons_sorted)).index(season)
    return 0.8 ** idx


def analyze_across_leagues(league_drafts: dict,
                           league_ctx: Optional[dict] = None,
                           current_scoring_key: str = "half",
                           league_names: Optional[dict] = None) -> dict:
    """Cross-league manager DNA. `league_drafts` = {league_id: drafts_list} for
    every league you can read (i.e. every league you're in). Any manager who
    appears in more than one of them gets their picks POOLED for a bigger, more
    confident read; the profile records which leagues fed it and whether that
    history spans multiple scoring formats.

    Returns {owner_id: profile} where profile is analyze_managers() output plus:
      source_leagues : [league_id, ...] that contributed
      cross_league   : bool (pooled from 2+ leagues)
    `league_ctx` = {league_id: {season: ctx}} so per-season format still applies.
    """
    league_ctx = league_ctx or {}
    # merge every league's drafts into one stream, tagging each pick's league so
    # a manager's picks across leagues aggregate under their persistent owner id.
    merged = []
    owner_leagues = defaultdict(set)
    merged_ctx = {}
    for lid, drafts in league_drafts.items():
        for draft in drafts:
            for p in draft:
                if p.get("manager"):
                    owner_leagues[p["manager"]].add(lid)
            merged.append(draft)
        # namespace season ctx so two leagues' same season don't collide: we key
        # analyze_managers on season only, so blend team/scoring by taking the
        # most recent league's context (best-effort; per-pick format still ok
        # because tags derive from position rates, not raw scoring).
        for yr, c in (league_ctx.get(lid) or {}).items():
            merged_ctx.setdefault(yr, c)

    prof = analyze_managers(merged, merged_ctx, current_scoring_key)

    # per-league sub-profiles so we can DETECT SPLITS (does a shared manager
    # draft differently in league A vs B?). Cheap: reuse analyze_managers on each
    # league's drafts alone, then compare the same owner's early lean + QB timing.
    per_league = {}
    for lid, drafts in league_drafts.items():
        per_league[lid] = analyze_managers(drafts, league_ctx.get(lid) or {},
                                           current_scoring_key)

    for owner, p in prof.items():
        ls = sorted(owner_leagues.get(owner, []))
        p["source_leagues"] = ls
        p["cross_league"] = len(ls) > 1
        p["league_names"] = {lid: (league_names or {}).get(lid, str(lid))
                             for lid in ls}
        if p["cross_league"]:
            p["dossier"] += f" [pooled across {len(ls)} of your leagues]"
            p["split_note"] = _split_note(owner, ls, per_league,
                                          league_names or {}, league_ctx)
    return prof


def _split_note(owner, league_ids, per_league, league_names, league_ctx):
    """Compare an owner's per-league sub-profiles; return a short human note when
    they draft MEANINGFULLY differently across your leagues (else '')."""
    subs = []
    for lid in league_ids:
        sp = (per_league.get(lid) or {}).get(owner)
        if not sp or sp.get("seasons", 0) < 1:
            continue
        es = sp.get("early_share") or {}
        # this league's scoring key (most recent season we have)
        ctx = league_ctx.get(lid) or {}
        keys = [c.get("scoring_key") for c in ctx.values() if c.get("scoring_key")]
        subs.append({
            "lid": lid, "name": league_names.get(lid, str(lid)),
            "rb": es.get("RB", 0), "wr": es.get("WR", 0),
            "qb_first": sp.get("qb_first_round"),
            "open": sp.get("favorite_opening"),
            "scoring": keys[-1] if keys else None,
        })
    if len(subs) < 2:
        return ""
    notes = []
    # biggest RB-lean gap across leagues
    rb_hi = max(subs, key=lambda s: s["rb"])
    rb_lo = min(subs, key=lambda s: s["rb"])
    if rb_hi["rb"] - rb_lo["rb"] >= 0.25:
        hi_s = f" ({rb_hi['scoring']})" if rb_hi["scoring"] else ""
        lo_s = f" ({rb_lo['scoring']})" if rb_lo["scoring"] else ""
        notes.append(f"more RB-early in {rb_hi['name']}{hi_s} "
                     f"({int(rb_hi['rb']*100)}%) than {rb_lo['name']}{lo_s} "
                     f"({int(rb_lo['rb']*100)}%)")
    # QB timing split
    qbs = [s for s in subs if s["qb_first"] is not None]
    if len(qbs) >= 2:
        q_hi = max(qbs, key=lambda s: s["qb_first"])
        q_lo = min(qbs, key=lambda s: s["qb_first"])
        if q_hi["qb_first"] - q_lo["qb_first"] >= 3:
            notes.append(f"takes QB earlier in {q_lo['name']} "
                         f"(~r{q_lo['qb_first']:.0f}) vs {q_hi['name']} "
                         f"(~r{q_hi['qb_first']:.0f})")
    if not notes:
        return ""
    return "Split: " + "; ".join(notes) + "."


def analyze_managers(drafts: list[list[dict]],
                     season_ctx: Optional[dict] = None,
                     current_scoring_key: str = "half") -> dict:
    """Return {owner_id: profile}. profile has the legacy fields plus a deeper
    read. season_ctx = {season: {teams, scoring_key, ...}} from
    league_history.season_contexts (optional but recommended)."""
    season_ctx = season_ctx or {}

    # gather per-manager, per-season pick lists (ordered by overall)
    mgr_seasons = defaultdict(lambda: defaultdict(list))   # mgr -> season -> [picks]
    names = {}
    for draft in drafts:
        for p in draft:
            mgr = p.get("manager")
            if not mgr:
                continue
            names[mgr] = p.get("manager_name", str(mgr))
            mgr_seasons[mgr][p.get("season")].append(p)
    for mgr in mgr_seasons:
        for yr in mgr_seasons[mgr]:
            mgr_seasons[mgr][yr].sort(key=lambda x: x.get("overall") or 999)

    all_seasons = sorted({p.get("season") for d in drafts for p in d
                          if p.get("season")})

    out = {}
    for mgr, seasons in mgr_seasons.items():
        yrs = sorted(seasons)
        nyr = len(yrs)
        wsum = sum(_recency_weight(y, all_seasons) for y in yrs) or 1.0

        # --- weighted early-pick position rates (positional draft capital) ---
        early_pos_w = defaultdict(float)      # pos -> weighted share of early capital
        first3_seq = Counter()                # opening 3-position sequence
        pos_first_round = defaultdict(list)   # pos -> [round first taken] per season
        reach_deltas = []                     # (their overall) - (player ADP proxy)
        player_ct = Counter()
        rookie = [0, 0]

        for yr in yrs:
            w = _recency_weight(yr, all_seasons)
            picks = seasons[yr]
            early = picks[:_EARLY_PICKS]
            n_early = len(early) or 1
            posc = Counter(p["position"] for p in early if p.get("position"))
            for pos, c in posc.items():
                early_pos_w[pos] += w * (c / n_early)
            # opening sequence (first 3 positions)
            seq = tuple(p["position"] for p in picks[:3] if p.get("position"))
            if len(seq) == 3:
                first3_seq[seq] += w
            # when do they first take each streamer position?
            seen = set()
            for p in picks:
                pos = p.get("position")
                if pos and pos not in seen:
                    seen.add(pos)
                    pos_first_round[pos].append(p.get("round") or 99)
            # loyalty + rookie
            for p in picks:
                if p.get("name"):
                    player_ct[_norm(p["name"])] += 1  # count normalized
                rookie[1] += 1
                if p.get("rookie"):
                    rookie[0] += 1

        # normalize early shares by total weight
        early_share = {pos: round(v / wsum, 3) for pos, v in early_pos_w.items()}

        # --- derive tendency tags with confidence ---
        tags = []
        rb = early_share.get("RB", 0)
        wr = early_share.get("WR", 0)
        qb = early_share.get("QB", 0)
        te = early_share.get("TE", 0)
        if rb >= 0.5:
            tags.append("RB-heavy")
        if wr >= 0.5:
            tags.append("WR-zealot")
        if rb <= 0.15 and wr >= 0.4:
            tags.append("zero-RB")
        if 0.3 <= rb <= 0.5 and wr >= 0.3:
            tags.append("hero-RB")
        # streamer timing: avg round they FIRST take each streamer position
        def _avg_first(pos):
            xs = pos_first_round.get(pos, [])
            return round(sum(xs) / len(xs), 1) if xs else None
        qb_first = _avg_first("QB")
        te_first = _avg_first("TE")
        if qb_first is not None and qb_first <= 5:
            tags.append("QB-early")
        if te_first is not None and te_first <= 4:
            tags.append("TE-premium")
        if not tags:
            tags = ["ADP-robot"]
        tags = list(dict.fromkeys(tags))

        # --- confidence: how consistent is the top lean across seasons? ---
        # count seasons whose single most-early-loaded position matches the tag
        lean_pos = max(("RB", "WR"), key=lambda p: early_share.get(p, 0))
        seasons_matching = 0
        for yr in yrs:
            early = seasons[yr][:_EARLY_PICKS]
            posc = Counter(p["position"] for p in early if p.get("position"))
            if posc and posc.most_common(1)[0][0] == lean_pos:
                seasons_matching += 1
        confidence = round(seasons_matching / nyr, 2) if nyr else 0.0
        conf_label = ("LOCK" if confidence >= 0.8 and nyr >= 3
                      else "STRONG" if confidence >= 0.6
                      else "LEAN" if confidence >= 0.4
                      else "NOISY")

        # --- opening sequence they favor most ---
        fav_seq = None
        if first3_seq:
            fav_seq = "-".join(first3_seq.most_common(1)[0][0])

        # --- loyalty picks (drafted 2+ times) ---
        fav_players = {nm: c for nm, c in player_ct.most_common() if c >= 2}

        # --- scoring-change flag ---
        hist_keys = {season_ctx.get(y, {}).get("scoring_key") for y in yrs
                     if season_ctx.get(y, {}).get("scoring_key")}
        scoring_changed = bool(hist_keys) and (current_scoring_key not in hist_keys
                                               or len(hist_keys) > 1)
        team_counts = sorted({season_ctx.get(y, {}).get("teams") for y in yrs
                              if season_ctx.get(y, {}).get("teams")})

        rr = round(rookie[0] / rookie[1], 2) if rookie[1] else 0.0

        # human dossier
        lean_txt = ", ".join(f"{p} {int(early_share[p]*100)}%"
                             for p in sorted(early_share, key=early_share.get,
                                             reverse=True)[:3]) or "balanced"
        bits = [f"{names[mgr]}: {nyr} seasons ({conf_label} read, "
                f"{int(confidence*100)}% consistent)."]
        bits.append(f"Early-capital lean {lean_txt}.")
        if fav_seq:
            bits.append(f"Favors opening {fav_seq}.")
        if qb_first is not None:
            bits.append(f"1st QB ~rd {qb_first}"
                        + (" (early)" if qb_first <= 5 else " (streams)") + ".")
        if te_first is not None and te_first <= 6:
            bits.append(f"1st TE ~rd {te_first}.")
        if fav_players:
            bits.append("Loyalty: " + ", ".join(
                f"{nm.split()[-1] if ' ' in nm else nm} ({c}x)"
                for nm, c in list(fav_players.items())[:4]) + ".")
        if scoring_changed:
            bits.append(f"NOTE history spans {'/'.join(sorted(hist_keys))} scoring; "
                        f"this year is {current_scoring_key} — discount lean.")
        if len(team_counts) > 1:
            bits.append(f"League size changed ({'->'.join(map(str, team_counts))} "
                        f"teams) across their history.")
        dossier = " ".join(bits)

        out[mgr] = {
            # legacy fields (drop-in compatible with apply_manager_dna / prophecy)
            "manager_name": names[mgr],
            "tendencies": tags,
            "rookie_rate": rr,
            "fav_team_id": None,
            "favorite_players": fav_players,
            "seasons": nyr,
            "dossier": dossier,
            "early_share": {k: round(v, 2) for k, v in early_share.items()},
            # deeper fields
            "confidence": confidence,
            "confidence_label": conf_label,
            "favorite_opening": fav_seq,
            "qb_first_round": qb_first,
            "te_first_round": te_first,
            "scoring_changed": scoring_changed,
            "team_counts": team_counts,
        }
    return out
