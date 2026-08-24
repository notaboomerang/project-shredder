"""
Championship-archetype scorer.

Grades a roster against the repeatable patterns of teams that WIN fantasy
leagues (not just make playoffs). Produces a 0-100 "title fit" score, a set
of component sub-scores, and actionable gap flags ("bench too safe — add a
ceiling swing").

Winning-roster patterns encoded (from public championship research):
  1. RB ANCHOR      - at least one top-tier RB for a scarce weekly floor.
  2. WR DEPTH       - a deep, startable WR corps (tradeable + bye-proof).
  3. VALUE QB/TE    - QB (1QB leagues) and non-elite TE drafted LATE, banking
                      early capital into RB/WR.
  4. CEILING BENCH  - bench skews to high-UPSIDE swings (rookies, ambiguous
                      backfields, post-hype), not safe veterans.
  5. PLAYOFF SLATE  - roster's key pieces have soft Wk15-17 pass/schedule.
  6. STRUCTURE      - starting slots actually filled; no dead roster spots.

This is the META template (no league history needed). League-specific trend
mining (learn_from_history in opponents.py) layers on top when available.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import engine as E
import matchups as M
import projections as P


@dataclass
class ArchetypeScore:
    total: float                       # 0-100 title-fit
    components: dict                   # sub-score per pattern
    flags: list[str] = field(default_factory=list)   # actionable gaps
    strengths: list[str] = field(default_factory=list)


def score_roster(roster_players: list[tuple[str, str]], pool: list[P.RawPlayer],
                 cfg: E.LeagueConfig, scoring_key: str = "half") -> ArchetypeScore:
    name_to_raw = {p.name: p for p in pool}
    # rank each rostered player within his position (for "top-tier" tests)
    pos_rank = _position_ranks(pool, cfg)

    have: dict[str, list[str]] = {}
    for n, pos in roster_players:
        have.setdefault(pos, []).append(n)

    comp: dict[str, float] = {}
    flags: list[str] = []
    strengths: list[str] = []

    # 1. RB ANCHOR (0-22): reward a top-8 RB; partial for top-15
    rb_best = _best_rank(have.get("RB", []), pos_rank)
    if rb_best and rb_best <= 8:
        comp["RB anchor"] = 22
        strengths.append(f"Elite RB anchor (RB{rb_best})")
    elif rb_best and rb_best <= 15:
        comp["RB anchor"] = 13
    else:
        comp["RB anchor"] = 4 if rb_best else 0
        if roster_players:
            flags.append("No elite RB anchor — you lack the scarce weekly RB floor")

    # 2. WR DEPTH (0-22): reward 3+ startable WRs, at least one top-12
    wrs = have.get("WR", [])
    wr_ranks = sorted(r for r in (pos_rank.get(n) for n in wrs) if r)
    startable = sum(1 for r in wr_ranks if r <= cfg.teams * 3)  # ~top-36 in 12tm
    top12 = any(r <= 12 for r in wr_ranks)
    comp["WR depth"] = min(22, 6 * startable + (6 if top12 else 0))
    if roster_players and startable < 3 and len(roster_players) >= 6:
        flags.append("Thin at WR — champions carry deep, startable WR corps")
    elif startable >= 3:
        strengths.append(f"Deep WR corps ({startable} startable)")

    # 3. VALUE QB/TE (0-16): penalize spending an EARLY pick on QB / non-elite TE
    qb_early = _drafted_early(have.get("QB", []), name_to_raw, scoring_key, thresh=60)
    te_early = _drafted_early(
        [n for n in have.get("TE", []) if (pos_rank.get(n) or 99) > 3],
        name_to_raw, scoring_key, thresh=60)
    penalty = 0
    if qb_early:
        penalty += 8
        flags.append("QB drafted early — that capital is better spent on RB/WR (1QB)")
    if te_early:
        penalty += 4
        flags.append("Non-elite TE drafted early — stream/late-draft TE instead")
    comp["Value QB/TE"] = max(0, 16 - penalty)
    if not qb_early and not te_early and roster_players:
        strengths.append("Banked early capital into RB/WR (late QB/TE)")

    # 4. CEILING (0-14): reward rookies / young upside anywhere on the roster
    #    (draft order can place a rookie in a starter slot; still upside).
    starter_cap = sum(cfg.starters.values())
    all_names = [n for n, _ in roster_players]
    bench_names = [n for n, _ in roster_players[starter_cap:]]
    ceiling = sum(1 for n in all_names
                  if (name_to_raw.get(n) and (name_to_raw[n].rookie or
                      (name_to_raw[n].age or 30) <= 24)))
    comp["Ceiling bench"] = min(14, 4 * ceiling)
    if bench_names and ceiling == 0:
        flags.append("Roster is all safe/veteran — add high-ceiling swings")
    elif ceiling >= 2:
        strengths.append(f"Ceiling upside ({ceiling} young/rookie swings)")

    # 5. PLAYOFF SLATE (0-14): avg playoff-week softness of your pass-game pieces
    po_softs = []
    for n, pos in roster_players:
        if pos in ("QB", "WR", "TE"):
            raw = name_to_raw.get(n)
            if raw:
                rpt = M.schedule_report(raw.team)
                if rpt:
                    po_softs.append(rpt.playoff_softness)
    if po_softs:
        avg_po = sum(po_softs) / len(po_softs)
        comp["Playoff slate"] = max(0, min(14, 7 + avg_po))
        if avg_po >= 3:
            strengths.append("Pass game set up to smash the fantasy playoffs")
        elif avg_po <= -3:
            flags.append("Tough playoff pass-D slate for your WR/QB — seek soft-slate pieces")
    else:
        comp["Playoff slate"] = 7  # neutral

    # 6. STRUCTURE (0-12): are dedicated starting slots on pace to be filled?
    need = _unfilled_starters(have, cfg)
    comp["Structure"] = max(0, 12 - 2 * need)
    if need and len(roster_players) >= sum(cfg.starters.values()):
        flags.append(f"{need} starting slot(s) still unfilled — fix roster structure")

    total = round(sum(comp.values()), 1)
    return ArchetypeScore(total=total, components={k: round(v, 1) for k, v in comp.items()},
                          flags=flags, strengths=strengths)


# --------------------------------------------------------------------------- helpers
def _position_ranks(pool: list[P.RawPlayer], cfg: E.LeagueConfig) -> dict[str, int]:
    ranks: dict[str, int] = {}
    by_pos: dict[str, list[tuple[str, float]]] = {}
    for p in pool:
        pts = E.project_points(p.stats, cfg.scoring)
        by_pos.setdefault(p.position, []).append((p.name, pts))
    for plist in by_pos.values():
        plist.sort(key=lambda x: x[1], reverse=True)
        for i, (name, _) in enumerate(plist, start=1):
            ranks[name] = i
    return ranks


def _best_rank(names: list[str], pos_rank: dict[str, int]) -> Optional[int]:
    rs = [pos_rank[n] for n in names if n in pos_rank]
    return min(rs) if rs else None


def _drafted_early(names: list[str], name_to_raw, scoring_key: str,
                   thresh: float) -> bool:
    """True if any of these players has an ADP inside `thresh` (i.e. is an
    early pick), used to flag over-investing in QB/TE."""
    for n in names:
        raw = name_to_raw.get(n)
        if raw:
            adp = P.adp_for(raw, scoring_key)
            if adp and adp <= thresh:
                return True
    return False


def _unfilled_starters(have: dict, cfg: E.LeagueConfig) -> int:
    need = 0
    for pos in ("QB", "RB", "WR", "TE", "K", "DST"):
        need += max(0, cfg.starters.get(pos, 0) - len(have.get(pos, [])))
    return need
