# Fantasy Football Draft Assistant — Research & Build Plan

_Compiled 2026-08-22 for a Monday-night live draft. All facts cited from live web sources; rankings move constantly so the app pulls live data, never hardcodes._

## 1. The core recommendation engine: VORP / Value-Based Drafting (VBD)

The entire "who should I pick" question reduces to one well-established idea: **value a player by how much he beats a freely-available replacement at his own position, not by raw projected points.** This is VORP (Value Over Replacement Player).

```
VORP = player_projected_points - replacement_level_points[position]
```

**Replacement level = the best player who will NOT be a starter in your league.** It depends on league size AND starting-lineup requirements. Standard baselines (source: sticktothemodel.com, 2025):

| League size | RB baseline | WR baseline | QB baseline | TE baseline |
|---|---|---|---|---|
| 10-team | RB21 | WR31 | QB11 | TE11 |
| 12-team | RB25 | WR37 | QB13 | TE13 |
| 14-team | RB29 | WR43 | QB15 | TE15 |

Better: compute the baseline **dynamically** from the league's actual roster settings rather than a lookup table:

```
baseline_rank[pos] = teams * (starters_at_pos + share_of_flex_and_bench_that_go_to_pos)
```
e.g. 12 teams × (2 RB starters + ~0.7 of a FLEX/bench absorbed by RB) ≈ RB25. The Sleeper draft object hands us `teams`, `slots_rb`, `slots_wr`, `slots_te`, `slots_qb`, `slots_flex`, `slots_bn`, `rounds` directly, so the baseline is computed, not guessed.

**Worked example (12-team):**
| Player | Pos | Proj pts | Replacement | VORP |
|---|---|---|---|---|
| McCaffrey | RB | 320 | 150 (RB25) | **170** |
| Kelce | TE | 240 | 110 (TE13) | **130** |
| Hill | WR | 290 | 170 (WR37) | **120** |
| Mahomes | QB | 380 | 310 (QB13) | **70** |

Mahomes scores the most raw points but is the *worst value* — replacement QBs are nearly as good. This is why "don't draft a QB early" is mathematically correct in 1-QB leagues, and why the app must never rank on raw points.

**Sources:** FantasyPros VBD guide (https://www.fantasypros.com/2025/06/fantasy-football-draft-strategy-value-based-drafting-vorp-vols-vona/), sticktothemodel VORP explainer (https://sticktothemodel.com/blog/fantasy-football-vorp-explained-2025), FantasyPros support (https://support.fantasypros.com/hc/en-us/articles/115005868747).

## 2. Scoring formats — the toggle you asked for (PPR / 0.5 / Standard)

Only one number changes across the three formats — **points per reception** — but it cascades through every valuation:

- **PPR (1.0/rec):** pass-catching RBs, slot WRs, and target-hogging TEs gain the most. WR VORP rises; deep WR pool.
- **Half-PPR (0.5/rec):** balanced; top RBs and WRs sit at similar VORP.
- **Standard (0.0/rec):** workhorse/goal-line RBs dominate; WR depth increases (more WRs become replacement-level viable); TE premium shrinks.

The reception point is a **per-player multiplier on projected receptions**, so the engine recomputes projected points → re-ranks → re-baselines the moment you flip the toggle. Players with big PPR-vs-Standard swings (high-reception, low-TD backs and slot WRs) are exactly where the format choice changes your pick.

Typical rest-of-scoring (configurable, pulled from the league object on Sleeper): pass yd 0.04, pass TD 4, rush/rec yd 0.1, rush/rec TD 6, INT −1/−2, fumble −2, plus K and DST.

## 3. Live draft connectivity — platform by platform

| Platform | Live draft API? | Auth | Library |
|---|---|---|---|
| **Sleeper** | ✅ **YES — public, documented, no auth** | none | direct HTTP / `sleeper-api-wrapper` |
| ESPN | ⚠️ undocumented v3 endpoints | cookies `espn_s2` + `SWID` | `espn-api` (cwendt94) |
| Yahoo | ⚠️ official API, painful | OAuth2 | `yahoofantasy`, `yfpy` |
| NFL.com | ❌ no usable public API | — | manual entry / scrape |

### Sleeper is the clear winner and should be the primary target
- Read-only HTTP, **no token, no login**. Base URL `https://api.sleeper.app/v1`.
- Rate limit: stay **under 1000 calls/min** or risk an IP block. Polling once every 3–5s during a draft is trivially safe.
- **Live draft state endpoints (the whole game):**
  - `GET /draft/<draft_id>` → settings: `teams`, `slots_*`, `rounds`, `pick_timer`, `draft_order`, `slot_to_roster_id`, `metadata.scoring_type` (ppr/half_ppr/std).
  - `GET /draft/<draft_id>/picks` → **every pick as it happens** — `player_id`, `pick_no`, `round`, `draft_slot`, `roster_id`, `picked_by`, plus `metadata` (name, team, position). Poll this to know who's gone and whose turn it is.
  - `GET /league/<league_id>/drafts` → find the draft_id from a league.
  - `GET /user/<username>` → resolve your user_id (to find your own picks/roster).
- `GET /players/nfl` → the player-id → name/position/team map (~5MB; **cache once/day**, never poll).
- `GET /players/nfl/trending/add` → bonus "buzz" signal.

Docs: https://docs.sleeper.com/

**Whose pick is it:** with `teams`, `draft_order`/`slot_to_roster_id`, and `pick_no` from `/picks`, snake order is pure arithmetic — round = ceil(pick_no/teams); within a round the slot alternates direction each round. So the app always knows the current pick, how many picks until my next turn, and therefore which players are likely to survive to me.

### Fallbacks (ESPN/Yahoo/NFL or a private- this-app-can't-see draft)
1. **ESPN:** `espn-api` (https://github.com/cwendt94/espn-api) reads live draft with `espn_s2`+`SWID` cookies (grab from browser dev tools). Undocumented but widely used.
2. **Yahoo:** `yahoofantasy` (https://github.com/mattdodge/yahoofantasy) / `yfpy` (https://github.com/uberfastman/yfpy) via OAuth2 — heavier setup.
3. **Manual mode (universal safety net):** a fast "mark drafted" board so if the API path fails or the league is on NFL.com, you tap each pick off as it happens and still get live recommendations. **This must exist regardless of platform** — it's the guaranteed-works path for Monday.

## 4. Projections + ADP data (free/programmatic)

- **FantasyPros** — consensus projections + Real-Time ADP for all 3 scoring formats; the reference source. Pages exist per format (https://www.fantasypros.com/nfl/adp/overall.php, .../real-time-adp/ppr, .../projections/qb.php?week=draft). Official REST API exists (paid/keyed: https://www.fantasypros.com/api-data/); free path is CSV/scrape.
- **FantasyFootballCalculator** — free ADP by format & team count (https://fantasyfootballcalculator.com/adp), has a simple JSON endpoint.
- **nflverse / `nfl_data_py`** — free historical + some projection data (Python/pip).
- **Sleeper `search_rank`** — every player object carries a rough rank; usable as a zero-dependency fallback ranking if we can't fetch projections in time.

**ADP is used two ways:** (1) as a fallback value signal, and (2) as **"value vs ADP" edge** — a player whose VORP rank is far ahead of his ADP is a value to grab; one going well before ADP is a reach to fade. And ADP tells the app *who will realistically still be there at my next pick*, which drives the "take him now vs wait" call.

## 5. Draft strategy the engine encodes

- **Tiers over ranks.** Group players into VORP tiers; the real decision is "is the last player in this tier about to disappear before my next pick?" Draft before a **cliff** (a big VORP drop between tiers).
- **Positional scarcity ordering** (typical top-5 VORP): RB 100–180 (scarcest) > TE 80–140 (top-heavy) > WR 80–130 > QB 40–80 (deep, stream-able).
- **Roster-need weighting.** Pure VORP early (rounds 1–5 take best value regardless of position); as your starters fill, weight by remaining needs and don't over-stack a full position.
- **Don't reach for QB/K/DST.** QB rounds ~8–12 in 1-QB; K/DST last two rounds. **Superflex/2-QB flips this** — QB VORP skyrockets, draft them early (detect from `slots_qb`≥2 or a SUPER_FLEX slot).
- **Bye weeks & handcuffs** as tiebreakers, not primary drivers: avoid stacking too many starters on one bye; consider handcuffing your own workhorse RB late.
- **Common mistakes to avoid (baked into warnings):** drafting on raw points, QB too early, ignoring league size in the baseline, over-drafting one position, forgetting FLEX math.

## 6. 2026 landscape (freshness flagged — DO NOT hardcode)

Current FantasyPros 2026 preseason consensus (as of Aug 2026, moves daily):
- **RB1s:** Gibbs, Bijan Robinson, Jonathan Taylor, James Cook, McCaffrey, Henry, Barkley, Jeanty (rookie).
- **WR1s:** Ja'Marr Chase, Puka Nacua, JSN, Amon-Ra St. Brown, CeeDee Lamb, Jefferson.
- **TE1s:** Bowers, McBride, Loveland (rookie), Kraft, Warren (rookie).
- **QB1s:** Josh Allen, Lamar Jackson, Drake Maye, Burrow, Jayden Daniels.

**These are illustrative only.** The app pulls current projections/ADP live at launch so it's never stale — hardcoded rankings would be wrong by Monday.

---

## Proposed architecture

**Streamlit app** (matches KC's existing LRDP toolchain — Python 3.13 has streamlit+requests):

```
fantasy_draft_assistant/
  app.py                  # Streamlit UI: setup sidebar + live board
  sleeper_client.py       # thin HTTP client for the Sleeper API (+ player cache)
  projections.py          # fetch/parse projections + ADP per scoring format
  scoring.py              # projected-points calc for PPR / 0.5 / Standard
  vorp.py                 # baseline computation + VORP + tiers + value-vs-ADP
  draft_state.py          # snake math: whose pick, picks-until-me, who's gone
  recommender.py          # combine VORP + roster needs + survival prob -> ranked recs
  data/players_cache.json # daily-cached Sleeper player map
  RESEARCH.md             # this file
```

**Two connection modes:**
1. **Sleeper live** — paste league/draft URL or username → auto-poll `/picks` every ~4s → board updates itself.
2. **Manual** — pick off a searchable list as each player goes; works for ESPN/Yahoo/NFL or any offline draft.

**The recommendation each pick:** ranked list of best available by VORP, grouped into tiers, annotated with (a) your positional needs, (b) whether the player survives to your next pick per ADP, (c) value-vs-ADP edge, (d) tier-cliff warnings ("last elite RB — take now"). Scoring toggle (PPR / 0.5 / Standard) and team-count/roster settings recompute everything live.
