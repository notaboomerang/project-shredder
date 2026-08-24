"""Compute real 2024 team tendencies from nflverse play-by-play and write
data/team_context.json (consumed by advanced_metrics for kicker/DST context)."""
import json
import os
import pandas as pd

CSV = os.path.join("data", "pbp_2024.csv")
OUT = os.path.join("data", "team_context.json")

# only the columns we need (keeps memory sane on a 388MB file)
cols = ["posteam", "down", "ydstogo", "yardline_100", "play_type",
        "field_goal_attempt", "rush_attempt", "pass_attempt", "punt_attempt",
        "touchdown", "td_team", "drive", "game_id", "fixed_drive_result",
        "qb_kneel", "qb_spike"]
df = pd.read_csv(CSV, usecols=lambda c: c in cols, low_memory=False)

teams = sorted(x for x in df["posteam"].dropna().unique() if isinstance(x, str))
ctx = {}

for tm in teams:
    t = df[df["posteam"] == tm]

    # --- 4th-down GO rate: on 4th down (excl kneels/spikes), how often did they
    # run a real offensive play (rush/pass) instead of punting or kicking a FG? ---
    fourth = t[(t["down"] == 4) & (t["qb_kneel"] != 1) & (t["qb_spike"] != 1)]
    # a "decision" down = they attempted FG, punt, or a go (rush/pass)
    went = fourth[(fourth["rush_attempt"] == 1) | (fourth["pass_attempt"] == 1)]
    kicked_or_punted = fourth[(fourth["field_goal_attempt"] == 1) |
                              (fourth["punt_attempt"] == 1)]
    denom = len(went) + len(kicked_or_punted)
    go_rate = round(len(went) / denom, 3) if denom else 0.28

    # --- red-zone TD rate: of red-zone (<=20 yd) drives, share ending in TD ---
    rz = t[t["yardline_100"] <= 20]
    rz_drives = rz.groupby(["game_id", "drive"])
    n_rz, n_rz_td = 0, 0
    for _, g in rz_drives:
        n_rz += 1
        res = str(g["fixed_drive_result"].iloc[-1]) if len(g) else ""
        if "Touchdown" in res:
            n_rz_td += 1
    rz_td_rate = round(n_rz_td / n_rz, 3) if n_rz else 0.55

    # --- scoring volume: team points/game (approx via TDs*6.7 + FG*3 per game) ---
    games = t["game_id"].nunique() or 1
    tds = int(((t["touchdown"] == 1) & (t["td_team"] == tm)).sum())
    fgs = int((t["field_goal_attempt"] == 1).sum())  # attempts (proxy)
    scoring_vol = round((tds * 6.7 + fgs * 2.6) / games, 1)

    ctx[tm] = {"go_rate": go_rate, "rz_td_rate": rz_td_rate,
               "scoring_vol": scoring_vol}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(ctx, f, indent=2, sort_keys=True)

# print a few notable ones to sanity-check
for tm in ("DET", "PHI", "BAL", "DAL", "GB", "KC", "SF"):
    if tm in ctx:
        c = ctx[tm]
        print(f"{tm}: go={c['go_rate']} rz_td={c['rz_td_rate']} vol={c['scoring_vol']}")
print(f"wrote {len(ctx)} teams -> {OUT}")
