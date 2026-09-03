"""
Defense vs. position — how generous is each defense to each fantasy position.

The question this answers (KC, 2026-09-03, "Tier A"): some defenses are soft
against wide receivers but stout against running backs; others are the reverse.
We want the team-defense-level matchup read — "is this a good spot for a WR
against this D" — to nudge betting-edge prop lines and start/sit color.

    NOT the specific-corner level (that needs coverage/participation attribution
    we confirmed is only ~49% populated and never gives a per-target CB→WR
    assignment). This is the ~70% version: TEAM defense vs POSITION.

HOW IT WORKS (from real nflverse play-by-play):
  1. Take every scoring-relevant play from the rolling window (same 2024+2025 →
     auto-roll seasons clutch_split uses).
  2. Credit the fantasy production to the player who earned it (receiver on a
     catch, rusher on a carry, passer on a pass) and tag the DEFENSE that
     allowed it. Player→position comes from the nflverse seasonal roster.
  3. Roll up by defense × position: total fantasy points allowed, and points
     PER GAME, plus targets/carries so volume is visible.
  4. Compare to the LEAGUE AVERAGE for that position. A defense giving up
     +6 PPR/g to WRs over league is a SOFT matchup; −6 is TOUGH.
  5. That per-position surplus/deficit feeds the read: a soft spot nudges a
     WR's prop / start-sit up, a tough spot down.

FANTASY SCORING is the app's own `engine.Scoring` / `project_points`, so the
points here match the rest of the tool and honor the league's PPR setting.

HONEST CAVEATS baked into the output:
  • Team-defense level, not per-CB. Answers "good spot for the position," not
    "which corner is on him."
  • Volume matters — a defense can allow few points because opponents rarely
    targeted the position, not because it covered well. We carry per-game AND
    per-target rates so low-volume ≠ elite coverage by mistake.

DATA POSTURE mirrors clutch_split.py / drive_data.py: nfl_data_py if present,
else the public nflverse parquet release directly; a shipped league-average
fallback keeps the feature working with no network / no package. Every number
degrades to the fallback, never raises. INFORMATIONAL CONTEXT only — a matchup
nudge, never a VORP bump.

SELF-TEST:  python defense_vs_position.py [SEASON ...]
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict
from typing import Optional

try:
    from engine import Scoring, project_points
except Exception:                                  # pragma: no cover
    Scoring = None                                 # type: ignore
    project_points = None                          # type: ignore

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_CACHE = os.path.join(_DATA_DIR, "defense_vs_position.json")

# The offensive positions we grade a defense against.
_POSITIONS = ("QB", "RB", "WR", "TE")

# League-average fantasy points ALLOWED per game to each position (half-PPR-ish,
# per team-defense), used only when pbp is unavailable or a cell is empty.
# Reflects well-known norms: QB ~18, RB ~22 (rush + rec), WR ~30 (12-team PPR
# aggregate across the position), TE ~9. These are per-POSITION-GROUP totals a
# defense faces in a game, not per-player.
_LEAGUE_FALLBACK: dict[str, dict] = {
    "QB": {"pts_per_game": 17.5, "pts_per_play": 0.42, "plays_per_game": 38.0},
    "RB": {"pts_per_game": 21.0, "pts_per_play": 0.60, "plays_per_game": 30.0},
    "WR": {"pts_per_game": 29.0, "pts_per_play": 0.70, "plays_per_game": 24.0},
    "TE": {"pts_per_game": 9.0,  "pts_per_play": 0.85, "plays_per_game": 8.0},
}


@dataclass
class PosAllowed:
    """One defense's production allowed to one position."""
    position: str
    pts_per_game: float          # fantasy points allowed to the position / game
    pts_per_play: float          # per targeted/rushed play (volume-neutral)
    plays_per_game: float        # targets+carries faced / game (the volume knob)
    n_plays: int
    n_games: int
    source: str = "fallback"     # "pbp" | "cache" | "fallback"


@dataclass
class DefenseProfile:
    """A defense's fantasy-points-allowed profile across positions."""
    team: str
    allowed: dict                # position -> PosAllowed
    source: str = "fallback"

    def get(self, pos: str) -> PosAllowed:
        return self.allowed.get(pos) or _fallback_allowed(pos)


def _fallback_allowed(pos: str) -> PosAllowed:
    fb = _LEAGUE_FALLBACK[pos]
    return PosAllowed(position=pos, pts_per_game=fb["pts_per_game"],
                      pts_per_play=fb["pts_per_play"],
                      plays_per_game=fb["plays_per_game"],
                      n_plays=0, n_games=0, source="fallback")


def _fallback_defense(team: str) -> DefenseProfile:
    return DefenseProfile(team=team,
                          allowed={p: _fallback_allowed(p) for p in _POSITIONS},
                          source="fallback")


# ---------------------------------------------------------------------------
# Season window + loaders — reuse clutch_split's rolling logic when importable
# so the two tools stay in lockstep on which seasons are "current".
# ---------------------------------------------------------------------------

def _normalize_seasons(season) -> list[int]:
    try:
        from clutch_split import _normalize_seasons as _cs_norm
        return _cs_norm(season)
    except Exception:
        pass
    # standalone fallback: mirror clutch_split's default behavior
    if season is None:
        import datetime as _dt
        now = _dt.date.today()
        cur = now.year if now.month >= 8 else now.year - 1
        return [cur - 1, cur - 2, cur - 3]   # conservative: assume pre-Week-1
    if isinstance(season, (list, tuple, set)):
        return sorted({int(y) for y in season}, reverse=True)
    return [int(season)]


def _season_weights(seasons: list[int]) -> dict[int, float]:
    try:
        from clutch_split import _season_weights as _cs_w
        return _cs_w(seasons)
    except Exception:
        ordered = sorted(seasons, reverse=True)
        raw = {y: 0.55 ** i for i, y in enumerate(ordered)}
        tot = sum(raw.values()) or 1.0
        return {y: w / tot for y, w in raw.items()}


def _load_pbp(year: int):
    try:
        from clutch_split import _load_pbp as _cs_pbp
        return _cs_pbp(year)
    except Exception:
        pass
    try:
        import pandas as pd
        url = ("https://github.com/nflverse/nflverse-data/releases/download/"
               f"pbp/play_by_play_{year}.parquet")
        return pd.read_parquet(url)
    except Exception:
        return None


_ROSTER_MEMO: dict[int, dict] = {}


def _load_positions(year: int) -> dict[str, str]:
    """gsis player_id -> position (QB/RB/WR/TE...) from the nflverse seasonal
    roster release. Memoized. Returns {} on any failure (callers degrade)."""
    if year in _ROSTER_MEMO:
        return _ROSTER_MEMO[year]
    mapping: dict[str, str] = {}
    try:
        import pandas as pd
        url = ("https://github.com/nflverse/nflverse-data/releases/download/"
               f"rosters/roster_{year}.parquet")
        r = pd.read_parquet(url, columns=["gsis_id", "position"])
        r = r.dropna(subset=["gsis_id", "position"])
        mapping = dict(zip(r["gsis_id"].astype(str), r["position"].astype(str)))
    except Exception:
        mapping = {}
    _ROSTER_MEMO[year] = mapping
    return mapping


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _load_cache() -> dict:
    if not os.path.exists(_CACHE):
        return {}
    try:
        with open(_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(d: dict) -> None:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_CACHE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Build from nflverse play-by-play
# ---------------------------------------------------------------------------

def build_defense_table(season=None, reception: float = 0.5,
                        use_cache: bool = True) -> dict[str, DefenseProfile]:
    """Per-defense fantasy-points-allowed-by-position profile, recency-weighted
    across the season window (same blend rule as clutch_split: each season
    aggregated on its own, then per-cell values weighted by recency × play
    count; n_plays/n_games summed). `reception` sets the PPR toggle (0/0.5/1.0)
    so points match the league. Never raises."""
    seasons = _normalize_seasons(season)
    ckey = "+".join(str(y) for y in seasons) + f"|r{reception}"

    if use_cache:
        cached = _load_cache()
        defs = cached.get("defenses") if isinstance(cached, dict) else None
        if defs and str(cached.get("_key")) == ckey:
            out = {}
            for t, poss in defs.items():
                out[t] = DefenseProfile(
                    team=t,
                    allowed={p: PosAllowed(**{**d, "source": "cache"})
                             for p, d in poss.items()},
                    source="cache")
            if out:
                return out

    weights = _season_weights(seasons)
    per_season: list[tuple[float, dict[str, DefenseProfile]]] = []
    for yr in seasons:
        pbp = _load_pbp(yr)
        if pbp is None:
            continue
        tbl = _aggregate_defense(pbp, _load_positions(yr), reception)
        if tbl:
            per_season.append((weights.get(yr, 0.0), tbl))

    if per_season:
        table = (per_season[0][1] if len(per_season) == 1
                 else _blend_tables(per_season))
        if table:
            _save_cache({"_key": ckey,
                         "defenses": {t: {p: asdict(pa) for p, pa in dp.allowed.items()}
                                      for t, dp in table.items()}})
            return table
    return {}


def _f(x) -> float:
    """NaN-/None-/numpy-safe float. Critical: `float(x or 0.0)` returns NaN for a
    NaN input (NaN is truthy) and a single NaN poisons every downstream mean, so
    every stat read from pbp MUST go through this."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v != v else v          # v != v is True only for NaN


def _score_line(rec, rec_yd, rec_td, rush_yd, rush_td, pass_yd, pass_td,
                interception, fumble_lost, reception: float) -> float:
    """Fantasy points for a stat line, via engine.project_points when available,
    else a hard-coded standard scoring identical to engine.Scoring defaults.
    All inputs are NaN-sanitized first."""
    rec, rec_yd, rec_td = _f(rec), _f(rec_yd), _f(rec_td)
    rush_yd, rush_td = _f(rush_yd), _f(rush_td)
    pass_yd, pass_td = _f(pass_yd), _f(pass_td)
    interception, fumble_lost = _f(interception), _f(fumble_lost)
    stats = {"rec": rec, "rec_yd": rec_yd, "rec_td": rec_td,
             "rush_yd": rush_yd, "rush_td": rush_td, "pass_yd": pass_yd,
             "pass_td": pass_td, "int": interception, "fumble_lost": fumble_lost}
    if project_points is not None and Scoring is not None:
        return project_points(stats, Scoring(reception=reception))
    return (rec * reception + rec_yd * 0.1 + rec_td * 6.0 + rush_yd * 0.1
            + rush_td * 6.0 + pass_yd * 0.04 + pass_td * 4.0
            + interception * -2.0 + fumble_lost * -2.0)


def _aggregate_defense(pbp, pos_map: dict[str, str],
                       reception: float) -> dict[str, DefenseProfile]:
    """Aggregate one season's pbp into fantasy points allowed by defense ×
    position. Regular season only (playoff samples are thin + non-representative
    for start/sit). Never raises; returns {} if required columns are absent."""
    import pandas as pd

    needed = {"defteam", "game_id", "receiver_player_id", "rusher_player_id",
              "receiving_yards", "rushing_yards", "pass_touchdown",
              "rush_touchdown", "complete_pass", "fumble_lost"}
    if not needed.issubset(set(pbp.columns)):
        return {}
    has_pass = {"passer_player_id", "passing_yards", "interception"}.issubset(set(pbp.columns))

    df = pbp.copy()
    if "season_type" in df.columns:
        df = df[df["season_type"] == "REG"]
    df = df[df["defteam"].notna()]
    if df.empty:
        return {}

    def pos_of(pid) -> Optional[str]:
        if pid is None or (isinstance(pid, float) and pd.isna(pid)):
            return None
        p = pos_map.get(str(pid))
        if p in ("HB", "FB"):
            return "RB"
        return p if p in _POSITIONS else None

    # accumulator: (defteam, position) -> {pts, plays, games:set}
    acc: dict[tuple, dict] = {}

    def bump(defteam, pos, pts, gid):
        if pos is None or defteam is None:
            return
        k = (defteam, pos)
        a = acc.setdefault(k, {"pts": 0.0, "plays": 0, "games": set()})
        a["pts"] += pts
        a["plays"] += 1
        a["games"].add(gid)

    for _, r in df.iterrows():
        defteam = r["defteam"]
        gid = r["game_id"]
        fl = _f(r.get("fumble_lost"))

        # Receiving credit: any target (complete or not) counts as a play faced;
        # points accrue on completions.
        rid = r.get("receiver_player_id")
        if rid is not None and not (isinstance(rid, float) and pd.isna(rid)):
            rpos = pos_of(rid)
            if rpos is not None:
                complete = _f(r.get("complete_pass"))
                rec_yd = _f(r.get("receiving_yards")) if complete else 0.0
                rec_td = _f(r.get("pass_touchdown")) if complete else 0.0
                pts = _score_line(rec=complete, rec_yd=rec_yd, rec_td=rec_td,
                                  rush_yd=0.0, rush_td=0.0, pass_yd=0.0,
                                  pass_td=0.0, interception=0.0,
                                  fumble_lost=0.0, reception=reception)
                bump(defteam, rpos, pts, gid)

        # Rushing credit
        ruid = r.get("rusher_player_id")
        if ruid is not None and not (isinstance(ruid, float) and pd.isna(ruid)):
            rupos = pos_of(ruid)
            if rupos is not None:
                pts = _score_line(rec=0.0, rec_yd=0.0, rec_td=0.0,
                                  rush_yd=_f(r.get("rushing_yards")),
                                  rush_td=_f(r.get("rush_touchdown")),
                                  pass_yd=0.0, pass_td=0.0, interception=0.0,
                                  fumble_lost=fl if rupos != "QB" else 0.0,
                                  reception=reception)
                bump(defteam, rupos, pts, gid)

        # Passing credit — the bulk of QB fantasy production a defense allows.
        # Credited to the passer (position-mapped, virtually always QB); one
        # dropback = one play faced.
        if has_pass:
            pid = r.get("passer_player_id")
            if pid is not None and not (isinstance(pid, float) and pd.isna(pid)):
                ppos = pos_of(pid)
                if ppos is not None:
                    pts = _score_line(rec=0.0, rec_yd=0.0, rec_td=0.0,
                                      rush_yd=0.0, rush_td=0.0,
                                      pass_yd=_f(r.get("passing_yards")),
                                      pass_td=_f(r.get("pass_touchdown")),
                                      interception=_f(r.get("interception")),
                                      fumble_lost=0.0, reception=reception)
                    bump(defteam, ppos, pts, gid)

    if not acc:
        return {}

    out: dict[str, DefenseProfile] = {}
    teams = sorted({k[0] for k in acc})
    for team in teams:
        allowed: dict[str, PosAllowed] = {}
        for pos in _POSITIONS:
            a = acc.get((team, pos))
            fb = _LEAGUE_FALLBACK[pos]
            if not a or a["plays"] <= 0:
                allowed[pos] = _fallback_allowed(pos)
                continue
            ng = max(1, len(a["games"]))
            allowed[pos] = PosAllowed(
                position=pos,
                pts_per_game=round(a["pts"] / ng, 2),
                pts_per_play=round(a["pts"] / a["plays"], 3),
                plays_per_game=round(a["plays"] / ng, 1),
                n_plays=a["plays"],
                n_games=ng,
                source="pbp")
        out[team] = DefenseProfile(team=team, allowed=allowed, source="pbp")
    return out


def _blend_tables(per_season: list) -> dict[str, DefenseProfile]:
    """Blend per-season defense tables, weighting each cell by (recency × plays).
    pts_per_game weighted by games; per-play/per-game rates weighted by plays;
    n_plays/n_games SUMMED. `per_season` = list of (weight, {team: DefenseProfile})."""
    teams = set()
    for _w, tbl in per_season:
        teams.update(tbl.keys())

    out: dict[str, DefenseProfile] = {}
    for team in sorted(teams):
        allowed: dict[str, PosAllowed] = {}
        for pos in _POSITIONS:
            num_ppg = den_game = 0.0
            num_ppp = num_ppl = den_play = 0.0
            tot_plays = tot_games = 0
            real = False
            for w, tbl in per_season:
                dp = tbl.get(team)
                if not dp or pos not in dp.allowed:
                    continue
                c = dp.allowed[pos]
                if c.n_plays <= 0:
                    continue
                real = True
                gw = w * c.n_games
                pw = w * c.n_plays
                num_ppg += c.pts_per_game * gw
                den_game += gw
                num_ppp += c.pts_per_play * pw
                num_ppl += c.plays_per_game * gw
                den_play += pw
                tot_plays += c.n_plays
                tot_games += c.n_games
            if not real:
                allowed[pos] = _fallback_allowed(pos)
                continue
            fb = _LEAGUE_FALLBACK[pos]
            allowed[pos] = PosAllowed(
                position=pos,
                pts_per_game=round(num_ppg / den_game, 2) if den_game else fb["pts_per_game"],
                pts_per_play=round(num_ppp / den_play, 3) if den_play else fb["pts_per_play"],
                plays_per_game=round(num_ppl / den_game, 1) if den_game else fb["plays_per_game"],
                n_plays=tot_plays, n_games=tot_games, source="pbp")
        out[team] = DefenseProfile(team=team, allowed=allowed, source="pbp")
    return out


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------

_MEMO: dict[str, dict[str, DefenseProfile]] = {}


def _key(season, reception) -> str:
    return "+".join(str(y) for y in _normalize_seasons(season)) + f"|r{reception}"


def get_defense(team: str, season=None, reception: float = 0.5) -> DefenseProfile:
    """A defense's points-allowed-by-position profile. Never raises; caches."""
    k = _key(season, reception)
    if k not in _MEMO:
        _MEMO[k] = build_defense_table(season, reception)
    return _MEMO[k].get((team or "").upper()) or _fallback_defense((team or "").upper())


_BASELINE_MEMO: dict[str, dict] = {}


def league_baseline(season=None, reception: float = 0.5) -> dict:
    """League-average points allowed per position across all defenses,
    game-weighted (matches how cells are built). Falls back to constants when no
    pbp is loaded. {pos: {pts_per_game, pts_per_play, plays_per_game, n_teams}}."""
    k = _key(season, reception)
    if k in _BASELINE_MEMO:
        return _BASELINE_MEMO[k]
    if k not in _MEMO:
        _MEMO[k] = build_defense_table(season, reception)
    table = _MEMO[k]

    base: dict[str, dict] = {}
    for pos in _POSITIONS:
        cells = [dp.allowed[pos] for dp in table.values()
                 if pos in dp.allowed and dp.allowed[pos].n_plays > 0]
        if not cells:
            fb = _LEAGUE_FALLBACK[pos]
            base[pos] = {**fb, "n_teams": 0}
            continue
        tg = sum(c.n_games for c in cells) or 1
        tp = sum(c.n_plays for c in cells) or 1
        base[pos] = {
            "pts_per_game": round(sum(c.pts_per_game * c.n_games for c in cells) / tg, 2),
            "pts_per_play": round(sum(c.pts_per_play * c.n_plays for c in cells) / tp, 3),
            "plays_per_game": round(sum(c.plays_per_game * c.n_games for c in cells) / tg, 1),
            "n_teams": len(cells),
        }
    _BASELINE_MEMO[k] = base
    return base


@dataclass
class MatchupNudge:
    """A defense-vs-position matchup read for one (defense, position)."""
    defense: str
    position: str
    pts_allowed_pg: float          # this D allows to the position / game
    league_pg: float               # league average for the position / game
    surplus_pg: float              # pts_allowed_pg - league_pg (+ = SOFT spot)
    softness: str                  # "SOFT" | "TOUGH" | "NEUTRAL"
    lean: float                    # start/sit or prop nudge in points (± )
    n_games: int
    source: str
    note: str


def matchup_nudge(defense: str, position: str, season=None,
                  reception: float = 0.5,
                  soft_threshold: float = 3.0) -> MatchupNudge:
    """The headline read: how soft/tough is `defense` for `position`, expressed
    as a points surplus vs the league and a bounded start/sit / prop lean.

    `soft_threshold` = points/game above (below) league to call a spot SOFT
    (TOUGH). `lean` is the raw surplus clamped to ±25% of the league norm so a
    thin-sample outlier can't produce an absurd nudge."""
    pos = (position or "").upper()
    dp = get_defense(defense, season, reception)
    cell = dp.get(pos)
    lg = league_baseline(season, reception).get(pos, _LEAGUE_FALLBACK[pos])
    lg_pg = lg["pts_per_game"]
    surplus = round(cell.pts_per_game - lg_pg, 2)

    cap = max(1.0, 0.25 * lg_pg)
    lean = round(max(-cap, min(cap, surplus)), 2)

    if cell.n_plays == 0:
        soft = "NEUTRAL"
        note = f"No {pos} sample vs {dp.team} — using league average."
    elif surplus >= soft_threshold:
        soft = "SOFT"
        note = (f"{dp.team} is a SOFT {pos} matchup — allows {cell.pts_per_game} "
                f"pts/g vs league {lg_pg} (+{surplus}); lean {pos} up {lean:+.1f}.")
    elif surplus <= -soft_threshold:
        soft = "TOUGH"
        note = (f"{dp.team} is a TOUGH {pos} matchup — allows {cell.pts_per_game} "
                f"pts/g vs league {lg_pg} ({surplus}); lean {pos} down {lean:+.1f}.")
    else:
        soft = "NEUTRAL"
        note = (f"{dp.team} is a neutral {pos} matchup — {cell.pts_per_game} "
                f"pts/g vs league {lg_pg} ({surplus:+.1f}).")

    return MatchupNudge(
        defense=(defense or "").upper(), position=pos,
        pts_allowed_pg=cell.pts_per_game, league_pg=lg_pg,
        surplus_pg=surplus, softness=soft, lean=lean,
        n_games=cell.n_games, source=cell.source, note=note)


def defense_report(defense: str, season=None,
                   reception: float = 0.5) -> dict:
    """Full four-position matchup read for one defense, ready to render."""
    dp = get_defense(defense, season, reception)
    base = league_baseline(season, reception)
    rows = {p: asdict(matchup_nudge(defense, p, season, reception))
            for p in _POSITIONS}
    return {"defense": (defense or "").upper(), "source": dp.source,
            "positions": rows, "league_baseline": base}


def position_ranking(position: str, season=None, reception: float = 0.5,
                     softest_first: bool = True) -> list[dict]:
    """All defenses ranked by how much they allow to `position` (softest first
    by default). Each row: {defense, pts_allowed_pg, surplus_pg, softness}."""
    pos = (position or "").upper()
    k = _key(season, reception)
    if k not in _MEMO:
        _MEMO[k] = build_defense_table(season, reception)
    table = _MEMO[k]
    lg = league_baseline(season, reception).get(pos, _LEAGUE_FALLBACK[pos])
    lg_pg = lg["pts_per_game"]
    rows = []
    for team, dp in table.items():
        c = dp.get(pos)
        if c.n_plays == 0:
            continue
        rows.append({"defense": team, "pts_allowed_pg": c.pts_per_game,
                     "surplus_pg": round(c.pts_per_game - lg_pg, 2),
                     "plays_per_game": c.plays_per_game, "n_games": c.n_games})
    rows.sort(key=lambda r: r["pts_allowed_pg"], reverse=softest_first)
    return rows


@dataclass
class MatchupSpot:
    """One player's matchup this week — the row of the screener."""
    player: str
    team: str
    position: str
    opponent: str                  # defense the player faces
    pts_allowed_pg: float          # what that D allows to the position / game
    league_pg: float
    surplus_pg: float              # + = soft (smash) · − = tough (avoid)
    softness: str                  # SOFT | TOUGH | NEUTRAL
    lean: float                    # bounded ± points nudge
    note: str


def matchup_screener(players, week=None, season=None, reception: float = 0.5,
                     top_n: int = 10, opponent_of=None, injury_of=None,
                     drop_out: bool = True):
    """Rank a slate of players by how good/bad their DEFENSE-VS-POSITION matchup
    is this week — the fantasy analog of "top-N biggest gaps."

    `players` — an iterable of objects (or dicts) each carrying a name, team, and
      position. Attributes `.name/.team/.position` are read first, then dict keys
      `name/team/position`.
    `opponent_of(team, week) -> opp_abbr` — resolves the defense a team faces this
      week. Defaults to matchups.load_schedule() when importable; a team on a bye
      (or unresolved) is skipped.
    `injury_of(name) -> injury-or-None` — resolves live injury status. Defaults to
      injuries.injury_for. When `drop_out` is True (default) a player flagged
      OUT / IR / PUP / SUS / DNR is EXCLUDED (a smash matchup is worthless if he
      can't play); Questionable/Doubtful are KEPT but tagged in `.note`.
    `week` — schedule week; defaults to the current NFL week (best-effort).

    Returns {"week", "smash": [MatchupSpot...], "avoid": [MatchupSpot...],
    "source"} — smash = top_n most-positive surplus, avoid = top_n most-negative.
    Never raises; players whose team/opponent/position can't resolve are dropped.
    """
    wk = week if week is not None else _current_week()

    if opponent_of is None:
        opponent_of = _default_opponent_of()
    if injury_of is None:
        injury_of = _default_injury_of()

    # chips that mean "not playing this week" — filtered out when drop_out
    _OUT_CHIPS = {"O", "IR", "PUP", "SUS", "DNR"}

    def _attr(p, name):
        v = getattr(p, name, None)
        if v is None and isinstance(p, dict):
            v = p.get(name)
        return v

    src = "fallback"
    spots: list[MatchupSpot] = []
    seen: set = set()
    for p in players or []:
        try:
            name = _attr(p, "name")
            team = (_attr(p, "team") or "").upper()
            pos = (_attr(p, "position") or "").upper()
            if not name or team == "" or pos not in _POSITIONS:
                continue
            key = (name, pos)
            if key in seen:            # one row per player-position
                continue

            # injury gate: drop players who can't play; tag those who might
            inj_tag = ""
            try:
                inj = injury_of(name)
            except Exception:
                inj = None
            if inj is not None:
                chip = getattr(inj, "chip", "") or ""
                if drop_out and chip in _OUT_CHIPS:
                    seen.add(key)      # resolved as unavailable — don't reconsider
                    continue
                if chip:
                    inj_tag = f" [{chip}]"

            opp = (opponent_of(team, wk) or "").upper()
            if not opp:                # bye / unresolved -> skip
                continue
            nudge = matchup_nudge(opp, pos, season, reception)
            if nudge.source in ("pbp", "cache"):
                src = "pbp"
            seen.add(key)
            spots.append(MatchupSpot(
                player=name, team=team, position=pos, opponent=opp,
                pts_allowed_pg=nudge.pts_allowed_pg, league_pg=nudge.league_pg,
                surplus_pg=nudge.surplus_pg, softness=nudge.softness,
                lean=nudge.lean,
                note=f"{name}{inj_tag} ({pos}, {team}) vs {opp}: {nudge.note}"))
        except Exception:
            continue

    smash = sorted([s for s in spots if s.surplus_pg > 0],
                   key=lambda s: s.surplus_pg, reverse=True)[:top_n]
    avoid = sorted([s for s in spots if s.surplus_pg < 0],
                   key=lambda s: s.surplus_pg)[:top_n]
    return {"week": wk, "smash": smash, "avoid": avoid, "source": src}


def _current_week() -> int:
    """Best-effort current NFL week (1-18). Regular season ≈ first Thu after Labor
    Day; before kickoff we return 1. Purely for schedule lookup, so an off-by-one
    near a boundary only shifts which opponent is read — never raises."""
    import datetime as _dt
    today = _dt.date.today()
    yr = today.year if today.month >= 8 else today.year - 1
    sep1 = _dt.date(yr, 9, 1)
    first_mon = sep1 + _dt.timedelta(days=(7 - sep1.weekday()) % 7)  # Labor Day
    kickoff = first_mon + _dt.timedelta(days=3)                       # Thu after
    if today < kickoff:
        return 1
    return min(18, (today - kickoff).days // 7 + 1)


def _default_opponent_of():
    """opponent_of(team, week) backed by matchups.load_schedule(); returns a
    resolver that yields '' when the schedule/team/week is unavailable (bye)."""
    try:
        from matchups import load_schedule
        sched = load_schedule()
    except Exception:
        sched = {}

    def _resolve(team, week):
        wk_map = sched.get((team or "").upper()) or sched.get(team) or {}
        opp = wk_map.get(week) or wk_map.get(int(week)) if wk_map else None
        return opp or ""
    return _resolve


def _default_injury_of():
    """injury_of(name) backed by injuries.injury_for(); returns a resolver that
    yields None (treat as healthy) when the injuries module isn't importable."""
    try:
        from injuries import injury_for
    except Exception:
        return lambda _name: None

    def _resolve(name):
        try:
            return injury_for(name)
        except Exception:
            return None
    return _resolve


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _print_defense(team: str, season, reception) -> None:
    r = defense_report(team, season, reception)
    print(f"\n== DEF {r['defense']}  (source={r['source']}) ==")
    hdr = f"{'pos':<5}{'pts/g':>8}{'league':>9}{'surplus':>9}{'soft?':>9}{'lean':>7}{'n_gm':>6}"
    print(hdr)
    print("-" * len(hdr))
    for p in _POSITIONS:
        d = r["positions"][p]
        print(f"{p:<5}{d['pts_allowed_pg']:>8.2f}{d['league_pg']:>9.2f}"
              f"{d['surplus_pg']:>+9.2f}{d['softness']:>9}{d['lean']:>+7.1f}{d['n_games']:>6}")


if __name__ == "__main__":
    _seasons = [int(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else None
    print(f"defense_vs_position self-test (seasons={_seasons or 'auto'})")
    for _t in ("DAL", "DEN", "KC", "SF", "NYJ"):
        _print_defense(_t, _seasons, 0.5)
    print("\n-- WR softest-first (top 8) --")
    for row in position_ranking("WR", _seasons)[:8]:
        print(f"  {row['defense']:<4} {row['pts_allowed_pg']:>6.2f} pts/g  "
              f"({row['surplus_pg']:+.2f})  n={row['n_games']}")

    # matchup_screener demo — synthetic slate + fixed opponent map so it runs
    # without depending on schedule data.
    print("\n-- matchup screener (synthetic slate) --")
    _slate = [
        {"name": "Ja'Marr Chase", "team": "CIN", "position": "WR"},
        {"name": "CeeDee Lamb", "team": "DAL", "position": "WR"},
        {"name": "Bijan Robinson", "team": "ATL", "position": "RB"},
        {"name": "Josh Allen", "team": "BUF", "position": "QB"},
        {"name": "Travis Kelce", "team": "KC", "position": "TE"},
        {"name": "Saquon Barkley", "team": "PHI", "position": "RB"},
    ]
    _opp = {"CIN": "DAL", "DAL": "CIN", "ATL": "KC", "BUF": "DEN",
            "KC": "ATL", "PHI": "KC"}
    res = matchup_screener(_slate, week=1, season=_seasons,
                           opponent_of=lambda t, w: _opp.get(t, ""), top_n=5)
    print(f"   week={res['week']} source={res['source']}")
    print("   SMASH:")
    for s in res["smash"]:
        print(f"     {s.player:18} {s.position} {s.team}->{s.opponent}  "
              f"{s.softness:7} surplus {s.surplus_pg:+.1f} lean {s.lean:+.1f}")
    print("   AVOID:")
    for s in res["avoid"]:
        print(f"     {s.player:18} {s.position} {s.team}->{s.opponent}  "
              f"{s.softness:7} surplus {s.surplus_pg:+.1f} lean {s.lean:+.1f}")
