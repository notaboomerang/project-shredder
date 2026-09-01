"""
Draft STRATEGY simulator for the Fantasy Draft Assistant.

Given the user's slot, walk their snake picks (cfg.my_overall_picks()) and, at
each of THEIR picks, take the best available player consistent with a chosen
strategy's positional rules. Between the user's picks, the OTHER teams are
simulated as taking the ADP-best available player (a light, deterministic model
of the room). The result is a projected starting lineup, its total projected
points, and a short text summary.

Strategies
----------
  zero-rb    - avoid RB in rounds 1-5, then hammer RB from round 6 on
  hero-rb    - take exactly ONE elite RB early, then lean WR until RB is thin
  robust-rb  - RB-RB with the first two picks, then best available
  bpa        - pure VORP, best player available every pick

Everything is deterministic (stable sorts, ADP/pos-rank tie-breaks) and
dependency-light: only `engine` and `projections` from this folder. No numpy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import engine as E
import projections as P


# ---------------------------------------------------------------------------
# scoring_key -> Scoring preset (mirrors edge_engine's convention)
# ---------------------------------------------------------------------------

def _scoring_from_key(scoring_key: str) -> E.Scoring:
    key = (scoring_key or "half").lower()
    mapping = {"ppr": "ppr", "full": "ppr", "fullppr": "ppr",
               "half": "half", "0.5ppr": "half", "halfppr": "half",
               "std": "std", "standard": "std", "nonppr": "std"}
    return E.Scoring.preset(mapping.get(key, "half"))


# The positions a strategy is allowed to draft with the user's picks.
STRATEGIES = ("zero-rb", "hero-rb", "robust-rb", "bpa")

# Positions we never spend a strategy pick on until late (streamed positions).
_LATE_ONLY = ("K", "DST")


@dataclass
class _Candidate:
    """A poolable player scored for THIS scoring format."""
    name: str
    position: str
    team: str
    proj_points: float
    adp: Optional[float]
    vorp: float = 0.0
    pos_rank: int = 0

    # deterministic ordering: best VORP first, then higher points, then
    # better (lower) ADP, then name for a total tie-break.
    def _vorp_key(self):
        return (-self.vorp, -self.proj_points,
                self.adp if self.adp is not None else 9e9, self.name)

    def _adp_key(self):
        return (self.adp if self.adp is not None else 9e9,
                -self.vorp, -self.proj_points, self.name)


@dataclass
class _SimResult:
    strategy: str
    picks: list = field(default_factory=list)          # [(overall, name, pos)]
    lineup: dict = field(default_factory=dict)          # slot -> (name, pos, pts)
    total_points: float = 0.0
    summary: str = ""


# ---------------------------------------------------------------------------
# candidate pool
# ---------------------------------------------------------------------------

def _build_candidates(pool: list, cfg: E.LeagueConfig,
                      scoring_key: str) -> list[_Candidate]:
    """Score every pool player for this format and attach VORP + pos_rank."""
    pvs: list[E.PlayerValue] = []
    meta: dict[str, tuple] = {}
    for raw in pool:
        pts = E.project_points(raw.stats, cfg.scoring)
        pvs.append(E.PlayerValue(raw.name, raw.name, raw.position, raw.team, pts))
        meta[raw.name] = (raw, pts)

    E.compute_vorp(pvs, cfg)   # sets vorp + pos_rank on the whole pool

    cands: list[_Candidate] = []
    for pv in pvs:
        raw, pts = meta[pv.name]
        cands.append(_Candidate(
            name=pv.name, position=pv.position, team=pv.team,
            proj_points=pts, adp=P.adp_for(raw, scoring_key),
            vorp=pv.vorp, pos_rank=pv.pos_rank,
        ))
    return cands


# ---------------------------------------------------------------------------
# strategy pick rules
# ---------------------------------------------------------------------------

def _rb_count(roster_positions: list[str]) -> int:
    return sum(1 for p in roster_positions if p == "RB")


def _need_caps(roster_positions: list[str], cfg: E.LeagueConfig,
               strategy: str = "bpa") -> set:
    """Positions still DRAFTABLE given what's on the roster AND the strategy —
    enforces a sane, strategy-distinct build. QB≤2, TE≤2, DST≤1, K≤1 always.
    RB/WR get STRATEGY-AWARE caps so the four builds actually differ:
      zero-rb  → WR heavy (RB≤3, WR≤7)
      hero-rb  → one anchor RB then WR (RB≤4, WR≤7)
      robust-rb→ RB heavy (RB≤7, WR≤5)
      bpa      → balanced (RB≤6, WR≤6)
    """
    have = {}
    for p in roster_positions:
        have[p] = have.get(p, 0) + 1
    rbwr = {
        "zero-rb":   {"RB": 3, "WR": 7},
        "hero-rb":   {"RB": 4, "WR": 7},
        "robust-rb": {"RB": 7, "WR": 5},
    }.get(strategy, {"RB": 6, "WR": 6})
    caps = {"QB": 2, "TE": 2, "DST": 1, "K": 1, **rbwr}
    ok = set()
    for pos in ("QB", "RB", "WR", "TE", "DST", "K"):
        if have.get(pos, 0) < caps.get(pos, 99):
            ok.add(pos)
    return ok


def _unfilled_starters(roster_positions: list[str], cfg: E.LeagueConfig) -> set:
    """Starter positions not yet filled — these get FORCED so the lineup is
    legal (no empty WR1/WR2/DST). FLEX is satisfied by any RB/WR/TE surplus."""
    have = {}
    for p in roster_positions:
        have[p] = have.get(p, 0) + 1
    st = cfg.starters
    need = set()
    for pos in ("QB", "RB", "WR", "TE", "DST", "K"):
        if have.get(pos, 0) < st.get(pos, 0):
            need.add(pos)
    return need


def _allowed_positions(strategy: str, rnd: int, roster_positions: list[str],
                       cfg: E.LeagueConfig) -> Optional[set]:
    """Positions the strategy permits for the user's pick this round, INTERSECTED
    with roster-need caps so the build stays legal (fills WR/DST, never hoards
    8 TE / 4 QB). K/DST only in the final two rounds regardless of strategy.
    """
    late_window = rnd >= cfg.rounds - 1     # last two rounds may grab K/DST
    core = {"QB", "RB", "WR", "TE"}

    if strategy == "zero-rb":
        # rounds 1-5: everything EXCEPT RB; round 6+: allow RB (and load it)
        allowed = core - {"RB"} if rnd <= 5 else core
    elif strategy == "hero-rb":
        rbs = _rb_count(roster_positions)
        if rnd == 1:
            allowed = {"RB"}                       # the one elite RB
        elif rbs >= 1 and rnd <= 6:
            allowed = core - {"RB"}                # then lean WR/TE/QB
        else:
            allowed = core                          # backfill RB later
    elif strategy == "robust-rb":
        rbs = _rb_count(roster_positions)
        if rnd <= 2 and rbs < 2:
            allowed = {"RB"}                        # RB-RB to open
        else:
            allowed = core
    else:  # bpa
        allowed = core

    if late_window:
        allowed = allowed | set(_LATE_ONLY)

    # ---- roster-need gate (the real fix) --------------------------------
    caps = _need_caps(roster_positions, cfg, strategy)
    allowed = allowed & caps                       # drop capped positions

    # NO second premium TE into the flex/bench: once the TE STARTER is filled,
    # a real manager fills flex with RB/WR, never a 2nd elite TE. Remove TE from
    # the allowed set unless doing so would leave nothing draftable.
    have = {}
    for p in roster_positions:
        have[p] = have.get(p, 0) + 1
    te_filled = have.get("TE", 0) >= cfg.starters.get("TE", 1)
    if te_filled and "TE" in allowed and (allowed - {"TE"}):
        allowed = allowed - {"TE"}

    # if a STARTER slot is still empty and we're running out of rounds, force it
    rounds_left = cfg.rounds - rnd + 1
    unfilled = _unfilled_starters(roster_positions, cfg) & caps
    # count non-K/DST starter needs; force them once rounds get tight
    skill_unfilled = unfilled - set(_LATE_ONLY)
    if skill_unfilled and rounds_left <= len(unfilled) + 1:
        allowed = skill_unfilled or allowed
    # always allow DST/K in the late window if still unfilled
    if late_window:
        allowed = allowed | (unfilled & set(_LATE_ONLY))

    return allowed or caps or core


def _pick_for_strategy(cands: list[_Candidate], strategy: str, rnd: int,
                       roster_positions: list[str],
                       cfg: E.LeagueConfig) -> Optional[_Candidate]:
    """Choose the user's player: best VORP among strategy-allowed positions."""
    if not cands:
        return None
    allowed = _allowed_positions(strategy, rnd, roster_positions, cfg)

    def eligible(c: _Candidate) -> bool:
        if allowed is None:
            return True
        return c.position in allowed

    picks = [c for c in cands if eligible(c)]
    if not picks:
        picks = list(cands)          # nothing allowed left -> take anyone
    picks.sort(key=lambda c: c._vorp_key())
    return picks[0]


def _replacement_baselines(cands, cfg):
    """Approx replacement-level projected points per position = the projection
    of the LAST starter-worthy player at that position across the league. Used
    so marginal value is measured OVER what you'd get later anyway (why a lone
    elite QB isn't worth a round-1 pick — QB replacement is high)."""
    teams = cfg.teams
    st = cfg.starters
    # rough starters-needed leaguewide per position (flex adds ~half to RB/WR)
    need = {
        "QB": st.get("QB", 1) * teams,
        "RB": int((st.get("RB", 2) + 0.6 * st.get("FLEX", 1)) * teams),
        "WR": int((st.get("WR", 2) + 0.4 * st.get("FLEX", 1)) * teams),
        "TE": st.get("TE", 1) * teams,
        "K": st.get("K", 1) * teams,
        "DST": st.get("DST", 1) * teams,
    }
    by_pos = {}
    for c in cands:
        by_pos.setdefault(c.position, []).append(c.proj_points)
    base = {}
    for pos, pts in by_pos.items():
        pts.sort(reverse=True)
        idx = min(need.get(pos, len(pts)), len(pts)) - 1
        base[pos] = pts[max(0, idx)] if pts else 0.0
    # STREAMABLE positions: you start only ONE and a top-tier late option scores
    # nearly as much, so their value-over-replacement should be SMALL. Set their
    # replacement to a SHALLOW rank (near the best) = a HIGH points floor, which
    # collapses elite-QB / elite-DST VOR. E.g. QB replacement = ~QB6's points, so
    # Josh Allen's edge over a streamed QB is small and RB/WR win round 1.
    shallow = {"QB": 6, "TE": 6, "DST": 4, "K": 4}
    for pos, rank in shallow.items():
        pts = by_pos.get(pos)
        if pts:
            base[pos] = pts[min(rank, len(pts)) - 1]
    return base


def _pick_best_lineup_value(cands, my_picked, rnd, cfg):
    """YOUR pick = whatever adds the most VALUE OVER REPLACEMENT to your starting
    lineup — not raw points, and not a strategy template. Measuring over
    replacement is why a lone elite QB (you start one, and QB12 still scores ~300)
    doesn't win round 1 over a scarce elite RB. A 3rd RB only counts if it beats
    your current RB2/FLEX. This is genuinely optimal weekly scoring.

    Rails: K/DST only the last two rounds, one each; never a 2nd QB / 3rd TE
    unless a starter slot is still empty."""
    if not cands:
        return None
    late = rnd >= cfg.rounds - 1
    have = {}
    for c in my_picked:
        have[c.position] = have.get(c.position, 0) + 1
    st = cfg.starters
    base_lineup, base_total = _build_lineup(my_picked, cfg)
    repl = _replacement_baselines(cands, cfg)

    caps = {"QB": 2, "TE": 2, "DST": 1, "K": 1}
    best, best_gain = None, -1e9
    for c in cands:
        pos = c.position
        # discipline rails
        if pos in _LATE_ONLY and not late:
            continue
        if have.get(pos, 0) >= caps.get(pos, 99):
            continue
        # a 2nd QB is never a starting-lineup upgrade — skip unless QB slot empty
        if pos == "QB" and have.get("QB", 0) >= st.get("QB", 1):
            continue
        # a 2nd TE almost never beats an RB/WR in the FLEX and TE is streamable —
        # skip once the TE starter slot is filled (unless TE is somehow still a
        # starter need). Mirrors the QB rail; kills the recurring "stacked TEs".
        if pos == "TE" and have.get("TE", 0) >= st.get("TE", 1):
            continue
        # raw lineup improvement from adding this player
        _, new_total = _build_lineup(my_picked + [c], cfg)
        raw_gain = new_total - base_total
        # VALUE OVER REPLACEMENT: discount by how good a late/streamed option at
        # this position would be. If the pick doesn't even crack the lineup
        # (raw_gain 0), it stays 0. Scarce positions (RB/WR) keep most of their
        # gain; deep/streamable ones (QB/TE/K/DST) shrink toward zero.
        if raw_gain > 0:
            gain = raw_gain - repl.get(pos, 0.0)
        else:
            gain = raw_gain          # bench depth: judged on raw contribution
        # tiny ADP tiebreaker so equal-gain picks prefer the higher-ranked player
        gain += (1.0 / ((c.adp or 400) + 5)) * 0.001
        if gain > best_gain:
            best, best_gain = c, gain
    if best is None:                 # everything capped → best remaining by points
        pool = [c for c in cands if late or c.position not in _LATE_ONLY] or list(cands)
        pool.sort(key=lambda c: (-c.proj_points, c.name))
        return pool[0]
    return best


def _pick_adp_best(cands: list[_Candidate], cfg: E.LeagueConfig,
                   rnd: int) -> Optional[_Candidate]:
    """Model an opposing team: take the ADP-best available skill player,
    holding K/DST until the last two rounds like a sensible manager."""
    if not cands:
        return None
    late_window = rnd >= cfg.rounds - 1
    pool = [c for c in cands
            if late_window or c.position not in _LATE_ONLY]
    if not pool:
        pool = list(cands)
    pool.sort(key=lambda c: c._adp_key())
    return pool[0]


def _pick_opponent(cands, cfg, overall, rnd, prof, loyal, opp_positions):
    """CRYSTAL-BALL opponent pick: score the board through THIS manager's DNA
    (tendency profile) + loyalty ('their guys') + ADP proximity, with roster
    need — the same logic Prophecy uses. Falls back to ADP-best when no profile
    is known, so 'who's left at your pick' reflects how your LEAGUE actually
    drafts, not a flat ADP curve."""
    import math
    if not cands:
        return None
    late_window = rnd >= cfg.rounds - 1
    # roster-need caps so opponents also build legal teams (no 8-TE bots)
    have = {}
    for p in opp_positions:
        have[p] = have.get(p, 0) + 1
    caps = {"QB": 2, "TE": 2, "DST": 1, "K": 1}
    best, best_s = None, -1.0
    for c in cands:
        pos = c.position
        if pos in _LATE_ONLY and not late_window:
            continue
        if have.get(pos, 0) >= caps.get(pos, 99):
            continue
        # PRIORITY: ESPN rankings (ADP) as the BASE — this is the opponents'
        # board, NOT our VORP. Then DNA (tendency) as the primary multiplier,
        # then loyalty as the override that can jump 'their guy' up the board.
        adp = c.adp if c.adp is not None else 400.0
        # base: better (lower) ADP = higher want, decaying with rank
        base = 1000.0 / (adp + 8.0)
        # only realistic once the pick is near/after the player's ADP (a real
        # room rarely reaches >1 round early except for a loyalty guy)
        if adp is not None and overall < adp - 12:
            base *= 0.35                 # too early — unlikely unless loyal
        s = base
        if prof:                          # DNA: primary lean multiplier
            s *= prof.pos_multiplier(pos, None, False)
        lc = loyal.get(_ln_name(c.name), 0) if loyal else 0
        if lc >= 2:                       # loyalty override: their 'guy'
            s *= 1.0 + 2.5 * lc
        s += c.vorp * 0.001               # VORP: negligible tiebreaker only
        if s > best_s:
            best, best_s = c, s
    if best is None:                     # everything capped → ADP-best fallback
        return _pick_adp_best(cands, cfg, rnd)
    return best


def _ln_name(s: str) -> str:
    import re
    s = (s or "").lower().strip()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# lineup construction
# ---------------------------------------------------------------------------

def _round_of(cfg: E.LeagueConfig, overall: int) -> int:
    return (overall - 1) // cfg.teams + 1


def _build_lineup(picked: list[_Candidate],
                  cfg: E.LeagueConfig) -> tuple[dict, float]:
    """Fill the config's starting slots greedily by projected points.

    Dedicated slots (QB/RB/WR/TE/K/DST) fill first from their own position,
    then flex-type slots pull the best leftover eligible player.
    """
    starters = cfg.starters
    by_pos: dict[str, list[_Candidate]] = {}
    for c in picked:
        by_pos.setdefault(c.position, []).append(c)
    for plist in by_pos.values():
        plist.sort(key=lambda c: (-c.proj_points, c.name))

    used: set[str] = set()
    lineup: dict[str, tuple] = {}

    def take(pos: str):
        for c in by_pos.get(pos, []):
            if c.name not in used:
                used.add(c.name)
                return c
        return None

    # 1) dedicated position slots
    for slot in ("QB", "RB", "WR", "TE", "K", "DST"):
        for i in range(starters.get(slot, 0)):
            c = take(slot)
            label = slot if starters.get(slot, 0) == 1 else f"{slot}{i + 1}"
            lineup[label] = ((c.name, c.position, round(c.proj_points, 1))
                             if c else (None, None, 0.0))

    # 2) flex-type slots pull best remaining eligible leftover
    for slot, count in starters.items():
        if slot not in E.FLEX_ELIGIBLE or not count:
            continue
        elig = E.FLEX_ELIGIBLE[slot]
        for i in range(count):
            best = None
            for pos in elig:
                for c in by_pos.get(pos, []):
                    if c.name in used:
                        continue
                    if best is None or c.proj_points > best.proj_points or (
                            c.proj_points == best.proj_points and c.name < best.name):
                        best = c
                    break  # by_pos already sorted; first unused is best for pos
            label = slot if count == 1 else f"{slot}{i + 1}"
            if best is not None:
                used.add(best.name)
                lineup[label] = (best.name, best.position,
                                 round(best.proj_points, 1))
            else:
                lineup[label] = (None, None, 0.0)

    total = round(sum(v[2] for v in lineup.values()), 1)
    return lineup, total


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def simulate_strategy(pool: list, cfg: E.LeagueConfig, strategy: str,
                      scoring_key: str, opponents=None,
                      loyalty_by_slot=None) -> dict:
    """Simulate ONE strategy from the user's slot.

    Opponents draft through the CRYSTAL BALL (each seat's learned DNA + loyalty
    'guys' + ADP), so 'who's left at your pick' mirrors how your real league
    drafts — not a flat ADP curve. Falls back to ADP-best for seats with no
    learned profile.

    Returns a dict with: strategy, picks, lineup, total_points, summary.
    """
    strategy = (strategy or "bpa").lower()
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; "
                         f"expected one of {STRATEGIES}")
    loyalty_by_slot = loyalty_by_slot or {}

    # score the format on a copy of the config so we never mutate the caller's
    sim_cfg = E.LeagueConfig(
        teams=cfg.teams, draft_slot=cfg.draft_slot, rounds=cfg.rounds,
        scoring=_scoring_from_key(scoring_key),
        starters=dict(cfg.starters), bench=cfg.bench,
    )

    cands = _build_candidates(pool, sim_cfg, scoring_key)
    avail: dict[str, _Candidate] = {c.name: c for c in cands}

    my_picks = set(sim_cfg.my_overall_picks())
    last_overall = sim_cfg.teams * sim_cfg.rounds

    my_picked: list[_Candidate] = []
    my_positions: list[str] = []
    pick_log: list[tuple] = []
    opp_positions: dict[int, list[str]] = {}   # slot -> [positions] for bots
    opp_picked: dict[int, list] = {}           # slot -> [_Candidate] for bots

    def _slot_of(ov):
        r = (ov - 1) // sim_cfg.teams + 1
        idx = (ov - 1) % sim_cfg.teams
        return idx + 1 if r % 2 == 1 else sim_cfg.teams - idx

    for overall in range(1, last_overall + 1):
        remaining = list(avail.values())
        if not remaining:
            break
        rnd = _round_of(sim_cfg, overall)
        if overall in my_picks:
            # YOUR pick = maximize projected starting-lineup points from the
            # available board (best-lineup-value), NOT a rigid strategy template.
            chosen = _pick_best_lineup_value(remaining, my_picked, rnd, sim_cfg)
            if chosen is not None:
                my_picked.append(chosen)
                my_positions.append(chosen.position)
                pick_log.append((overall, chosen.name, chosen.position))
        else:
            # CRYSTAL BALL: this opponent seat drafts via its DNA + loyalty
            _sl = _slot_of(overall)
            _prof = (opponents.profiles.get(_sl)
                     if opponents and hasattr(opponents, "profiles") else None)
            _loyal = loyalty_by_slot.get(_sl, {})
            _opos = opp_positions.setdefault(_sl, [])
            chosen = _pick_opponent(remaining, sim_cfg, overall, rnd,
                                    _prof, _loyal, _opos)
            if chosen is not None:
                _opos.append(chosen.position)
                opp_picked.setdefault(_sl, []).append(chosen)
        if chosen is not None:
            avail.pop(chosen.name, None)

    lineup, total = _build_lineup(my_picked, sim_cfg)
    summary = _summarize(strategy, pick_log, lineup, total)

    # build every OPPONENT team's roster + lineup (owner DNA drove their picks)
    all_teams = []
    my_slot = sim_cfg.draft_slot
    for sl in range(1, sim_cfg.teams + 1):
        if sl == my_slot:
            tl, tt = lineup, total
            picks = [(p[1], p[2]) for p in pick_log]
            owner = "YOU"
        else:
            picked = opp_picked.get(sl, [])
            tl, tt = _build_lineup(picked, sim_cfg)
            picks = [(c.name, c.position) for c in picked]
            owner = None
            if opponents and hasattr(opponents, "profiles"):
                prof = opponents.profiles.get(sl)
                owner = getattr(prof, "name", None) if prof else None
            owner = owner or f"Slot {sl}"
        all_teams.append({
            "slot": sl, "owner": owner, "is_me": sl == my_slot,
            "lineup": tl, "total_points": tt, "picks": picks,
        })

    return {
        "strategy": strategy,
        "picks": pick_log,
        "lineup": lineup,
        "total_points": total,
        "summary": summary,
        "all_teams": all_teams,
    }


def compare_strategies(pool: list, cfg: E.LeagueConfig,
                       scoring_key: str, opponents=None,
                       loyalty_by_slot=None) -> list[dict]:
    """Return the SINGLE optimal build: your picks maximize projected
    starting-lineup points at every turn, while opponents draft realistically
    off their DNA + loyalty + ADP. (No longer four rigid strategy templates —
    the sim just finds the best team you can actually assemble from your slot.)"""
    r = simulate_strategy(pool, cfg, "bpa", scoring_key,
                          opponents=opponents, loyalty_by_slot=loyalty_by_slot)
    r["strategy"] = "optimal"
    r["rank"] = 1
    return [r]


# ---------------------------------------------------------------------------
# summary text
# ---------------------------------------------------------------------------

_STRAT_BLURB = {
    "zero-rb": "Punted RB early, loaded WR/TE, then flooded RB from round 6.",
    "hero-rb": "Anchored one elite RB, then leaned WR before backfilling RB.",
    "robust-rb": "Opened RB-RB for a heavy backfield, then best available.",
    "bpa": "Pure value-based drafting - best VORP every pick.",
}


def _summarize(strategy: str, pick_log: list, lineup: dict,
               total: float) -> str:
    pos_counts: dict[str, int] = {}
    for _, _, pos in pick_log:
        pos_counts[pos] = pos_counts.get(pos, 0) + 1
    shape = ", ".join(f"{pos_counts[p]} {p}"
                      for p in ("QB", "RB", "WR", "TE", "K", "DST")
                      if pos_counts.get(p))
    first3 = " -> ".join(f"{n} ({p})" for _, n, p in pick_log[:3])
    blurb = _STRAT_BLURB.get(strategy, "")
    return (f"{strategy.upper()}: {blurb} "
            f"Roster shape: {shape}. "
            f"Opened with {first3}. "
            f"Projected starting lineup = {total} pts.")


if __name__ == "__main__":   # pragma: no cover - manual smoke test
    _pool = P.load_players(prefer_live=False)
    _cfg = E.LeagueConfig.monday()
    for _r in compare_strategies(_pool, _cfg, "half"):
        print(f"#{_r['rank']} {_r['strategy']:<10} {_r['total_points']:>7} pts")
        print("   " + _r["summary"])
