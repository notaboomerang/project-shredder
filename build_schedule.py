"""
Build data/schedule.json from the real nflverse schedule release.

matchups.load_schedule() reads data/schedule.json FIRST (falling back to the
seeded SCHEDULE_2026 only if it's absent), so writing this file makes the whole
schedule-driven layer — pass-D softness, venue, and the defense-vs-position
matchup screener — use the REAL opponent each team faces, not the plausible
seed. Re-run it each season (or whenever the schedule updates); it self-selects
the current season the same way the pbp loader does.

    python build_schedule.py [SEASON]

Data source: nflverse-data `schedules/games.parquet` (same trusted release the
pbp loader uses). Regular season only (game_type == 'REG'); a bye week is simply
absent from a team's map, which load_schedule/consumers already treat as a bye.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_OUT = os.path.join(_DATA_DIR, "schedule.json")
_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
        "schedules/games.parquet")


def _default_season() -> int:
    now = _dt.date.today()
    return now.year if now.month >= 8 else now.year - 1


def build(season: int | None = None) -> dict:
    """Fetch the nflverse schedule and return {TEAM: {week:int -> OPP}} for the
    season, REG games only. Raises on fetch/parse failure so the caller sees it."""
    import pandas as pd

    yr = season or _default_season()
    df = pd.read_parquet(_URL)
    df = df[(df["season"] == yr) & (df["game_type"] == "REG")]
    df = df.dropna(subset=["home_team", "away_team", "week"])

    sched: dict[str, dict[int, str]] = {}
    for _, r in df.iterrows():
        wk = int(r["week"])
        home = str(r["home_team"]).upper()
        away = str(r["away_team"]).upper()
        sched.setdefault(home, {})[wk] = away
        sched.setdefault(away, {})[wk] = home
    return sched


def main(argv) -> int:
    season = int(argv[1]) if len(argv) > 1 else None
    yr = season or _default_season()
    try:
        sched = build(season)
    except Exception as ex:  # noqa: BLE001
        print(f"FAILED to build schedule for {yr}: {ex}")
        return 1
    if not sched:
        print(f"No REG games found for {yr} — nothing written.")
        return 1

    os.makedirs(_DATA_DIR, exist_ok=True)
    # JSON keys must be strings; matchups.load_schedule() casts weeks back to int.
    serializable = {tm: {str(wk): opp for wk, opp in wks.items()}
                    for tm, wks in sched.items()}
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=0, sort_keys=True)

    teams = len(sched)
    games = sum(len(w) for w in sched.values()) // 2
    sample = next(iter(sorted(sched)))
    print(f"Wrote {_OUT}")
    print(f"  season {yr}: {teams} teams, {games} REG games")
    print(f"  e.g. {sample} wk1 -> {sched[sample].get(1, 'BYE')}, "
          f"byes are absent weeks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
