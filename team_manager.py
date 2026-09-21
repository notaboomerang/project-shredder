"""Team Management page (in-season command center).

Loads your ALREADY-DRAFTED roster from a connected ESPN league (no live draft
needed) and, per team you manage, answers three questions:

  1. Who to START  — LO.optimize_week over your real roster, with reasons.
  2. Who's about to ERUPT on my bench — EW.eruption_watch filtered to your
     benched players, so you don't leave a smash on the pine.
  3. Who to PICK UP  — WV.find_waiver_targets over the free-agent pool, cross-
     referenced with EW.eruption_watch so adds predicted to smash get a 🌋 badge.

Every erupt call rendered here is written to the prediction LEDGER
(erupt_ledger) so "you said X would smash" becomes verifiable — the ledger
scores each call HIT/MISS once real weekly points land.

The page is decoupled: it takes the pool/cfg/scoring/espn client as arguments
so it has no hard dependency on app.py globals and is unit-testable.
"""
from __future__ import annotations

from typing import Optional

import streamlit as st

import lineup_optimizer as LO
import waiver as WV
import eruption_watch as EW
import erupt_ledger as LEDGER


def _pos_of(pool, name: str) -> str:
    for p in pool:
        if p.name == name:
            return p.position
    return ""


def _erupt_index(pool, roster_names, week, season, scoring_key):
    """Run Eruption Watch over a set of players (by name), return {name: spot}."""
    reception = 1.0 if scoring_key == "ppr" else (0.5 if scoring_key == "half"
                                                  else 0.0)
    players = [p for p in pool if p.name in roster_names]
    try:
        res = EW.eruption_watch(players, week=int(week), season=season,
                                reception=reception, top_n=len(players) or 1)
    except Exception:
        return {}, None
    idx = {s.player: s for s in res.get("spots", [])}
    return idx, res


def render(ss, pool, cfg, scoring_key: str, espn=None,
           default_week: int = 1) -> None:
    st.markdown("### 🧠 Team Management")
    st.caption("Your drafted rosters, pulled live from ESPN. Start/sit, "
               "erupt-on-your-bench, and waiver adds predicted to smash 🌋 — "
               "every erupt call logged to the ledger.")

    if espn is None:
        st.info("Connect your ESPN league from the sidebar (**Setup / Connect "
                "ESPN**) to load your drafted rosters here.")
        return

    # ---- load my teams (cached in session so we don't refetch each rerun) ----
    tc = st.columns([1, 1, 3])
    wk = tc[0].number_input("NFL week", 1, 18, int(default_week), key="tm_week")
    if tc[1].button("🔄 Reload rosters"):
        ss.pop("_tm_rosters", None)

    if "_tm_rosters" not in ss:
        with st.spinner("Loading your rosters from ESPN…"):
            try:
                ss["_tm_rosters"] = espn.team_rosters(mine_only=True)
            except Exception as ex:  # noqa: BLE001
                st.error(f"Couldn't load rosters from ESPN: {ex}")
                return

    data = ss.get("_tm_rosters") or {}
    my_teams = [t for t in data.get("teams", []) if t.get("is_mine")] \
        or data.get("teams", [])
    if not my_teams:
        st.warning("No teams found for your ESPN login in this league. "
                   "Make sure the connected league is one you manage.")
        return

    season = int(getattr(espn, "season", 2026))

    # team picker (multiple leagues/teams supported)
    labels = [f'{t["team_name"]}  ·  {len(t["players"])} players'
              for t in my_teams]
    pick = st.selectbox("Team", range(len(my_teams)),
                        format_func=lambda i: labels[i], key="tm_team_pick")
    team = my_teams[pick]
    roster = team["players"]
    roster_pairs = [(p["name"], p["position"]) for p in roster]
    roster_names = {p["name"] for p in roster}

    st.markdown(f"#### {team['team_name']}")

    # ============================ 1) START / SIT ============================
    st.markdown("##### ✅ Start / Sit")
    try:
        rows = LO.optimize_week(roster_pairs, pool, cfg, int(wk), scoring_key)
    except Exception as ex:  # noqa: BLE001
        st.error(f"Couldn't build the lineup: {ex}")
        rows = []

    # eruption index over my whole roster (drives both the bench-erupt section
    # and a ceiling nudge badge on the start/sit rows)
    erupt_idx, erupt_res = _erupt_index(pool, roster_names, wk, season,
                                        scoring_key)

    if rows:
        starters = [s for s in rows if getattr(s, "started", False)]
        bench = [s for s in rows if not getattr(s, "started", False)]
        mc = st.columns([1, 1, 1])
        mc[0].metric("Projected starters",
                     round(sum(getattr(s, "weekly_points", 0) for s in starters), 1))
        mc[1].metric("Bench players", len(bench))
        mc[2].metric("On bye",
                     sum(1 for s in rows if "ON BYE" in getattr(s, "narrative", "")))

        for s in starters:
            spot = erupt_idx.get(s.name)
            erupt = f"  ·  🌋 +{spot.ceiling_boost:g}" if spot else ""
            cap = ""
            if spot and getattr(spot, "cast_quality", ""):
                cap = f"  ·  ⚠️ {spot.cast_quality}"
            st.markdown(f"**START · {getattr(s,'slot','')}** — {s.name} "
                        f"({s.position}){erupt}{cap}  \n"
                        f"<span style='color:#8a8a94;font-size:12px'>"
                        f"{getattr(s,'narrative','')}</span>",
                        unsafe_allow_html=True)
        with st.expander(f"Bench ({len(bench)})"):
            for s in bench:
                st.caption(f"SIT — {s.name} ({s.position}) · "
                           f"{getattr(s,'narrative','')}")

    # ==================== 2) ERUPT ON MY BENCH (don't sit a smash) ==========
    st.markdown("##### 🌋 Erupting on your bench")
    st.caption("Benched players whose ceiling is lighting up this week — "
               "consider starting them. A ⚠️ QB/cast flag means the ceiling is "
               "capped by who's throwing the ball.")
    started_names = {getattr(s, "name", "") for s in rows
                     if getattr(s, "started", False)}
    bench_erupts = [erupt_idx[nm] for nm in roster_names
                    if nm in erupt_idx and nm not in started_names]
    bench_erupts.sort(key=lambda s: s.ceiling_boost, reverse=True)
    if bench_erupts:
        for spot in bench_erupts[:8]:
            cap = (f"  ·  ⚠️ {spot.cast_quality}"
                   if getattr(spot, "cast_quality", "") else "")
            st.markdown(f"🌋 **{spot.player}** ({spot.position}, {spot.team}) "
                        f"vs {spot.opponent} — ceiling +{spot.ceiling_boost:g}"
                        f"{cap}  \n<span style='color:#8a8a94;font-size:12px'>"
                        f"{spot.note}</span>", unsafe_allow_html=True)
    else:
        st.caption("No benched player is flagged to erupt this week. Your "
                   "start/sit is already capturing the upside.")

    # ==================== 3) WAIVER ADDS PREDICTED TO ERUPT =================
    st.markdown("##### 📈 Waiver adds predicted to smash")
    st.caption("Top free-agent pickups ranked by the pickup score (ROS value + "
               "opportunity). A 🌋 badge = the ceiling model also flags them to "
               "smash THIS week; ⚠️ = ceiling capped by a shaky QB/cast.")

    # rostered = everyone on any team in the league (so FA = pool − rostered).
    # We only fetched my teams; fetch the full rostered set for accuracy.
    rostered = _all_rostered(ss, espn)
    try:
        targets = WV.find_waiver_targets(pool, cfg, rostered,
                                         scoring_key=scoring_key, top_n=25,
                                         my_roster=roster_pairs)
    except Exception as ex:  # noqa: BLE001
        st.error(f"Couldn't build waiver targets: {ex}")
        targets = []

    # eruption over the free-agent pool, so adds can carry a 🌋 badge
    fa_names = {t.name for t in targets}
    fa_erupt_idx, _ = _erupt_index(pool, fa_names, wk, season, scoring_key)

    shown = 0
    for t in targets:
        spot = fa_erupt_idx.get(t.name)
        badge = f"  ·  🌋 +{spot.ceiling_boost:g}" if spot else ""
        cap = (f"  ·  ⚠️ {spot.cast_quality}"
               if spot and getattr(spot, "cast_quality", "") else "")
        star = "  ·  ⭐" if getattr(t, "star", False) else ""
        st.markdown(f"**{t.name}** ({t.position}) · {t.priority}"
                    f"{badge}{cap}{star}  \n"
                    f"<span style='color:#8a8a94;font-size:12px'>"
                    f"{'; '.join(getattr(t,'reasons',[])[:3])}</span>",
                    unsafe_allow_html=True)
        shown += 1
        if shown >= 15:
            break
    if not shown:
        st.caption("No clear waiver upgrades right now.")

    # ---- log every erupt call this render made to the ledger ----
    all_spots = list(erupt_idx.values()) + list(fa_erupt_idx.values())
    if all_spots:
        n = LEDGER.log_calls(all_spots, season=season, week=int(wk))
        if n:
            st.caption(f"📓 Logged {n} erupt calls to the ledger for week {wk}.")

    # ---- ledger recap + hit-rate ----
    _render_ledger(espn, season, int(wk))


def _all_rostered(ss, espn) -> set:
    """Every rostered player name across the whole league (for FA = pool − this).
    Cached in session; falls back to just-my-teams if the full pull fails."""
    if "_tm_all_rostered" in ss:
        return ss["_tm_all_rostered"]
    names: set[str] = set()
    try:
        rp = espn.rostered_players()
        names = set(rp.get("names") or [])
    except Exception:
        data = ss.get("_tm_rosters") or {}
        for t in data.get("teams", []):
            names.update(p["name"] for p in t.get("players", []))
    ss["_tm_all_rostered"] = names
    return names


def _render_ledger(espn, season: int, week: int) -> None:
    st.markdown("##### 📓 Eruption ledger — did the calls hit?")
    st.caption("Every erupt call this app makes is recorded here so the "
               "prediction is verifiable. Once real weekly points land, each "
               "call is scored HIT/MISS.")

    cols = st.columns([1, 1, 2])
    if cols[0].button("Score last week vs actuals"):
        try:
            pts = espn.player_week_points(week - 1) if week > 1 else {}
            import espn_client as EC
            n = LEDGER.score_week(pts, season=season, week=week - 1,
                                  norm_fn=EC._norm_name)
            st.success(f"Scored {n} calls for week {week - 1}.")
        except Exception as ex:  # noqa: BLE001
            st.error(f"Couldn't score: {ex}")

    hr = LEDGER.hit_rate(season=season)
    m = st.columns([1, 1, 1, 1])
    m[0].metric("Calls logged", hr["total"])
    m[1].metric("Scored", hr["scored"])
    rate = f'{hr["hit_rate"]*100:.0f}%' if hr["hit_rate"] is not None else "—"
    m[2].metric("Hit rate", rate)
    m[3].metric("Pending", hr["pending"])

    recent = LEDGER.load(season=season)[:20]
    if recent:
        with st.expander(f"Recent calls ({len(recent)})"):
            for r in recent:
                res = r.get("result") or "pending"
                dot = {"HIT": "🟢", "MISS": "🔴"}.get(res, "⚪")
                actual = (f" · {r['actual_points']} pts"
                          if r.get("actual_points") is not None else "")
                cap = f" · ⚠️ {r['cast_quality']}" if r.get("cast_quality") else ""
                st.caption(f"{dot} W{r['week']} {r['player']} "
                           f"({r['position']}) · +{r['ceiling_boost']:g}"
                           f"{actual}{cap}")
