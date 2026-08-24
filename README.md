# 🥊 Project Shredder

A live-draft copilot for ESPN fantasy football. Runs locally as a Streamlit app,
connects read-only to your ESPN league, and recommends picks that build the
highest-scoring roster consistently — grounded in VORP, opponent DNA, and real data.

// live-draft copilot · VORP + edge engine · opponent DNA · prophecy · draft with soul — shred the board

## What it does

- **Board** — ranked players with plain-English badges, live tier-cliff alarm, a
  reconstructing draft queue that plans your next snake picks, and an opponent-watch view.
- **Opponent DNA + Prophecy** — learns each rival's drafting tendencies from your
  league's past drafts and predicts who they'll take before your next pick.
- **Roster Lab** — tier cliffs, playoff-slate, handcuffs, bye collisions, stacking.
- **Season-long tools** — weekly start/sit, trade evaluator, waiver wire, rankings,
  a shadow ledger, live scores + betting-line edge, and a 5-year value study.

The core rule: every pick is scored on **VORP** (value over replacement at position).
Situational/correlation signals are surfaced as context only — never folded into a
player's value.

## Run it

```bash
pip install streamlit requests pandas
streamlit run app.py
```

Then open http://127.0.0.1:8501 (or whatever port Streamlit prints).

## Files NOT in this repo (excluded by .gitignore)

These are intentionally left out — you provide them locally:

| File | What it is | How to restore |
|------|-----------|----------------|
| `data/espn_cookies.json` | Your ESPN `espn_s2` + `SWID` login cookies | Connect your league in the app's sidebar (the app writes it) |
| `data/leagues.json` | Your saved ESPN league IDs | Recreated as you add leagues in the app |
| `data/pbp_2024.csv` | ~99MB nflverse play-by-play | Download from nflverse (used for the value study / 4th-down tendencies) |
| `assets/*.mp4`, `assets/vo/` | Demo videos & narration | Optional — not needed to run |

**Never commit `espn_cookies.json`** — anyone with those cookies can access your ESPN account.
