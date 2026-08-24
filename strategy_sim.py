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


def _allowed_positions(strategy: str, rnd: int, roster_positions: list[str],
                       cfg: E.LeagueConfig) -> Optional[set]:
    """Positions the strategy permits for the user's pick this round.

    Returns None to mean "no positional constraint" (pure best available).
    K/DST are only ever allowed in the final two rounds regardless of strategy.
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
    return allowed


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
                      scoring_key: str) -> dict:
    """Simulate ONE strategy from the user's slot.

    Returns a dict with: strategy, picks, lineup, total_points, summary.
    """
    strategy = (strategy or "bpa").lower()
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; "
                         f"expected one of {STRATEGIES}")

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

    for overall in range(1, last_overall + 1):
        remaining = list(avail.values())
        if not remaining:
            break
        rnd = _round_of(sim_cfg, overall)
        if overall in my_picks:
            chosen = _pick_for_strategy(remaining, strategy, rnd,
                                        my_positions, sim_cfg)
            if chosen is not None:
                my_picked.append(chosen)
                my_positions.append(chosen.position)
                pick_log.append((overall, chosen.name, chosen.position))
        else:
            chosen = _pick_adp_best(remaining, sim_cfg, rnd)
        if chosen is not None:
            avail.pop(chosen.name, None)

    lineup, total = _build_lineup(my_picked, sim_cfg)
    summary = _summarize(strategy, pick_log, lineup, total)

    return {
        "strategy": strategy,
        "picks": pick_log,
        "lineup": lineup,
        "total_points": total,
        "summary": summary,
    }


def compare_strategies(pool: list, cfg: E.LeagueConfig,
                       scoring_key: str) -> list[dict]:
    """Run all four strategies and rank them by starting-lineup points desc."""
    results = [simulate_strategy(pool, cfg, s, scoring_key) for s in STRATEGIES]
    results.sort(key=lambda r: (-r["total_points"], r["strategy"]))
    for rank, r in enumerate(results, start=1):
        r["rank"] = rank
    return results


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
