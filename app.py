"""
Fantasy Draft Assistant — Level 100 live draft board.

Run:  streamlit run app.py   (use the Python 3.13 env with streamlit+requests)

Three surfaces:
  1. Sidebar  — DYNAMIC league config (scoring / teams / slot / lineup / bench),
                connection mode (ESPN live-connect or Manual), your roster.
  2. Board    — best-available ranked by the Edge Engine composite, with edge
                badges, tiers, survival %, value-vs-ADP; your pick-timing banner.
  3. Stack Lab— evaluate any QB + WR/TE stack's schedule softness (art+science).
"""
from __future__ import annotations

import streamlit as st

import engine as E
import projections as P
import edge_engine as X
import matchups as M
import opponents as O
import archetype as A
import strategy_sim as SIM
import lineup_optimizer as LO
import schedules_all as SCH
import dark_horse as DH
import soul as SOUL
import waiver as WV
import shredder_rankings as SR
import shadow_ledger as SLG
import live_games as LG
import situational as SIT
import situational_lookup as SITL
import roster_lab as RLAB
import season_tools as SEA
import draft_queue as DQ
import simulate as SIMU
import copilot as CO
import prophecy as PROPH
import league_history as LH
import wheel_play as WP
import mock_draft as MOCK

# fold all-32-team schedules into matchups so venue/pass-D work league-wide
try:
    SCH.merge_into_matchups()
except Exception:
    pass

try:
    import espn_client as EC
except Exception:
    EC = None

import saved_leagues as SL
import secrets_store as SEC
import espn_login as ELOGIN

import os as _os
_ICON = _os.path.join(_os.path.dirname(__file__), "assets", "shredder_icon.png")
st.set_page_config(page_title="Project Shredder", layout="wide",
                   page_icon=(_ICON if _os.path.exists(_ICON) else "🎸"),
                   initial_sidebar_state="expanded")

# --------------------------------------------------------------------------- slick theme
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=JetBrains+Mono:wght@500;700;800&family=Space+Grotesk:wght@500;700&display=swap');
:root{
  /* punk-rock analytics: near-black neutral base, acid accents, hairlines */
  --bg:#0a0a0c; --panel:#111114; --panel2:#17171b; --line:#2a2a30;
  --txt:#ececef; --dim:#8a8a95; --accent:#e8ff53; --accent2:#ff2e88;
  --good:#3ddc84; --warn:#ffb020; --bad:#ff4d5e; --gold:#e8ff53;
}
html,body,[class*="css"]{font-family:'Inter',system-ui,sans-serif;}
.stApp{background:
  linear-gradient(rgba(232,255,83,.015) 1px, transparent 1px) 0 0/100% 3px,
  radial-gradient(900px 500px at 85% -12%, #14140f 0%, var(--bg) 60%);}
/* header — zine masthead */
.hero{background:linear-gradient(100deg,#0f0f10 0%,#141410 55%,#170d13 100%);
  border:1px solid var(--line);border-left:3px solid var(--accent);
  border-radius:6px;padding:16px 22px;margin-bottom:14px;
  box-shadow:0 6px 34px rgba(0,0,0,.6);}
.hero h1{font-family:'Space Grotesk','Inter',sans-serif;font-size:26px;font-weight:700;
  margin:0;letter-spacing:-.5px;text-transform:uppercase;
  background:linear-gradient(90deg,#e8ff53,#ff2e88);-webkit-background-clip:text;
  -webkit-text-fill-color:transparent;}
.hero .sub{color:var(--dim);font-size:12px;margin-top:3px;
  font-family:'JetBrains Mono',monospace;letter-spacing:.3px;}
/* metric tiles */
[data-testid="stMetric"]{background:var(--panel);border:1px solid var(--line);
  border-radius:6px;padding:12px 14px;box-shadow:none;}
[data-testid="stMetricValue"]{font-family:'JetBrains Mono',monospace;font-size:22px;
  font-weight:800;color:var(--txt);}
[data-testid="stMetricLabel"]{color:var(--dim);font-weight:600;
  text-transform:uppercase;font-size:11px;letter-spacing:.5px;}
/* player card container */
[data-testid="stVerticalBlockBorderWrapper"]{background:var(--panel);
  border:1px solid var(--line)!important;border-radius:6px;
  transition:border-color .12s, transform .08s;}
[data-testid="stVerticalBlockBorderWrapper"]:hover{border-color:var(--accent)!important;
  transform:translateY(-1px);box-shadow:-2px 0 0 var(--accent2);}
/* tabs — index-card row */
.stTabs [data-baseweb="tab-list"]{gap:4px;background:transparent;}
.stTabs [data-baseweb="tab"]{background:var(--panel);border:1px solid var(--line);
  border-radius:4px 4px 0 0;padding:8px 15px;font-weight:600;color:var(--dim);
  text-transform:uppercase;font-size:12px;letter-spacing:.4px;}
.stTabs [aria-selected="true"]{background:var(--panel2);color:var(--accent)!important;
  border-bottom:2px solid var(--accent);}
/* buttons */
.stButton>button{border-radius:5px;border:1px solid var(--line);font-weight:700;
  background:var(--panel2);color:var(--txt);transition:all .1s;
  text-transform:uppercase;font-size:12px;letter-spacing:.3px;}
.stButton>button:hover{border-color:var(--accent);color:var(--accent);
  box-shadow:inset 0 0 0 1px var(--accent);}
/* badges (rendered via markdown spans) */
.bdg{display:inline-block;font:700 10px/1.5 'JetBrains Mono';padding:2px 8px;
  margin:2px 3px 2px 0;border-radius:3px;border:1px solid var(--line);white-space:nowrap;
  text-transform:uppercase;letter-spacing:.3px;}
.bdg-val{background:rgba(61,220,132,.12);color:var(--good);border-color:rgba(61,220,132,.45);}
.bdg-urgent{background:rgba(255,77,94,.12);color:var(--bad);border-color:rgba(255,77,94,.45);}
.bdg-wait{background:rgba(138,138,149,.10);color:var(--dim);}
.bdg-cliff{background:rgba(232,255,83,.12);color:var(--accent);border-color:rgba(232,255,83,.45);}
.bdg-stack{background:rgba(255,46,136,.14);color:var(--accent2);border-color:rgba(255,46,136,.45);}
.bdg-soft{background:rgba(232,255,83,.10);color:var(--accent);border-color:rgba(232,255,83,.35);}
.bdg-neutral{background:var(--panel2);color:var(--dim);}
.pill{font:800 12px 'JetBrains Mono';padding:3px 9px;border-radius:4px;}
.pos-RB{background:rgba(61,220,132,.15);color:var(--good);}
.pos-WR{background:rgba(232,255,83,.15);color:var(--accent);}
.pos-TE{background:rgba(255,46,136,.15);color:var(--accent2);}
.pos-QB{background:rgba(255,176,32,.15);color:var(--warn);}
.pos-K,.pos-DST{background:var(--panel2);color:var(--dim);}
.inj{font:800 10px 'JetBrains Mono';padding:2px 7px;border-radius:3px;margin-left:6px;}
.inj-out{background:rgba(255,77,94,.2);color:var(--bad);border:1px solid var(--bad);}
.inj-doubt{background:rgba(255,176,32,.18);color:var(--warn);border:1px solid var(--warn);}
.inj-quest{background:rgba(232,255,83,.15);color:var(--accent);border:1px solid var(--accent);}
.pname{font-family:'Space Grotesk','Inter',sans-serif;font-weight:700;font-size:16px;color:var(--txt);}
.pmeta{color:var(--dim);font-size:12px;font-family:'JetBrains Mono',monospace;}
/* multiselect chips — lime fill needs BLACK text or it's unreadable */
[data-baseweb="tag"]{background:var(--accent)!important;border-radius:4px!important;}
[data-baseweb="tag"] span,[data-baseweb="tag"] div{color:#0a0a0c!important;
  font-family:'JetBrains Mono',monospace!important;font-weight:700!important;}
[data-baseweb="tag"] svg{fill:#0a0a0c!important;color:#0a0a0c!important;}
[data-baseweb="tag"] [role="button"]:hover{background:rgba(0,0,0,.18)!important;}
/* sidebar input boxes — hairline white border so they're visible on black */
section[data-testid="stSidebar"] [data-baseweb="input"],
section[data-testid="stSidebar"] [data-baseweb="select"]>div,
section[data-testid="stSidebar"] [data-baseweb="base-input"],
section[data-testid="stSidebar"] .stNumberInput div[data-baseweb="input"]{
  border:1px solid rgba(255,255,255,.35)!important;border-radius:5px!important;
  background:var(--panel)!important;}
section[data-testid="stSidebar"] [data-baseweb="input"]:focus-within,
section[data-testid="stSidebar"] [data-baseweb="select"]>div:focus-within{
  border-color:var(--accent)!important;}
/* number-input +/- steppers: hairline border too */
section[data-testid="stSidebar"] .stNumberInput button{
  border:1px solid rgba(255,255,255,.30)!important;}
</style>
""", unsafe_allow_html=True)


def _badge_class(b: str) -> str:
    u = b.upper()
    if "VALUE +" in u:
        return "bdg-val"
    if "WON'T LAST" in u or "REACH" in u or "INJURY" in u or "BAD O-LINE" in u:
        return "bdg-urgent"
    if "CAN WAIT" in u:
        return "bdg-wait"
    if "TIER CLIFF" in u:
        return "bdg-cliff"
    if "STACK" in u:
        return "bdg-stack"
    if "SOFT" in u or "DOME" in u or "PACE" in u or "PASS-HEAVY" in u:
        return "bdg-soft"
    return "bdg-neutral"


def _badges_html(badges: list) -> str:
    return "".join(
        f'<span class="bdg {_badge_class(b)}">{b}</span>' for b in badges) or \
        '<span class="bdg bdg-neutral">—</span>'

SCORING_KEYS = {"Standard (non-PPR)": "std", "Half PPR (0.5)": "half", "Full PPR (1.0)": "ppr"}
SCORING_PRESET = {"std": "std", "half": "half", "ppr": "ppr"}


# --------------------------------------------------------------------------- state
def _init_state():
    ss = st.session_state
    ss.setdefault("drafted", set())              # names off the board
    ss.setdefault("my_roster", [])               # list of (name, position)
    ss.setdefault("current_overall", 1)          # overall pick number on the clock
    ss.setdefault("espn", None)                  # EspnClient instance
    ss.setdefault("espn_status", "")
    ss.setdefault("opponents", None)             # LeagueOpponents (built lazily)
    ss.setdefault("espn_auto", False)            # auto-poll toggle
    ss.setdefault("espn_sync_note", "")          # last sync status
    ss.setdefault("pick_log", [])                # [(overall, name, pos, slot)]
    ss.setdefault("team_rosters", {})            # slot -> [names]
    ss.setdefault("undo_stack", [])              # snapshots for undo
    ss.setdefault("regret", [])                  # [(passed_name, taken_at, by)]
    ss.setdefault("espn_s2", "")                 # persisted cookies (session only)
    ss.setdefault("espn_swid", "")
    # auto-load cookies from the local file (or browser) so you never re-type them
    if not ss.espn_s2 and not ss.espn_swid and not ss.get("_cookie_autoload_done"):
        try:
            _s2, _swid, _src = SEC.auto_load()
            if _s2 or _swid:
                ss.espn_s2, ss.espn_swid = _s2, _swid
                ss["_cookie_source"] = _src
        except Exception:
            pass
        ss["_cookie_autoload_done"] = True


_init_state()
ss = st.session_state


# --------------------------------------------------------------------------- sidebar
st.sidebar.title("⚙️ League Setup")
st.sidebar.caption("Everything here is dynamic — change it and the board re-ranks.")

use_monday = st.sidebar.toggle("Load Monday preset (ESPN, 12-team, .5 PPR, pick 11)", value=True)
mon = E.LeagueConfig.monday()

# ---- league-preloaded settings (from ESPN mSettings on Connect) ----
_lg = ss.get("_league_settings")   # dict from EspnClient.settings_profile()
_use_lg = False
if _lg:
    _use_lg = st.sidebar.checkbox(
        f"🔒 Use {_lg.get('league_name') or 'league'} settings "
        f"({_lg['teams']}-team · {_lg.get('scoring_format','?')})",
        value=True,
        help="Auto-loaded from ESPN. Uncheck to set teams/scoring/lineup by hand.")

def _lg_def(key, fallback):
    """League value when locked-in, else the manual fallback."""
    return (_lg.get(key) if (_use_lg and _lg and _lg.get(key) is not None)
            else fallback)

scoring_label = st.sidebar.selectbox("Scoring", list(SCORING_KEYS.keys()),
                                     index=1, disabled=_use_lg)
scoring_key = SCORING_KEYS[scoring_label]
teams = st.sidebar.number_input("Teams", 4, 16,
                                int(_lg_def("teams", mon.teams if use_monday else 12)),
                                disabled=_use_lg)
slot = st.sidebar.number_input("Your draft slot", 1, int(teams),
                               mon.draft_slot if use_monday else 1)
rounds = st.sidebar.number_input("Rounds", 8, 25, mon.rounds if use_monday else 16)

# lineup defaults: league slots when locked, else Monday/manual
_lgs = (_lg.get("starters") if (_use_lg and _lg and _lg.get("starters")) else None)
def _slot_def(pos, fb):
    return int(_lgs.get(pos, 0)) if _lgs else fb

st.sidebar.markdown("**Starting lineup**"
                    + ("  ·  🔒 from ESPN" if _use_lg and _lgs else ""))
c1, c2 = st.sidebar.columns(2)
qb = c1.number_input("QB", 0, 3, _slot_def("QB", 1), disabled=_use_lg and bool(_lgs))
rb = c2.number_input("RB", 0, 5, _slot_def("RB", 2), disabled=_use_lg and bool(_lgs))
wr = c1.number_input("WR", 0, 5, _slot_def("WR", 2), disabled=_use_lg and bool(_lgs))
te = c2.number_input("TE", 0, 3, _slot_def("TE", 1), disabled=_use_lg and bool(_lgs))
flex = c1.number_input("FLEX", 0, 3, _slot_def("FLEX", 1), disabled=_use_lg and bool(_lgs))
superflex = c2.number_input("SUPERFLEX", 0, 2, _slot_def("SUPERFLEX", 0),
                            disabled=_use_lg and bool(_lgs))
dst = c1.number_input("D/ST", 0, 2, _slot_def("DST", 1), disabled=_use_lg and bool(_lgs))
k = c2.number_input("K", 0, 2, _slot_def("K", 1), disabled=_use_lg and bool(_lgs))
bench = st.sidebar.number_input("Bench", 0, 12,
                                int(_lg_def("bench", 7)), disabled=_use_lg and bool(_lg and _lg.get("bench")))
prefer_floor = st.sidebar.toggle("📊 Prioritize consistency (weekly floor)",
                                 value=False,
                                 help="Rank steady week-to-week producers over "
                                      "boom/bust — 'highest-scoring consistently'.")

starters = {"QB": qb, "RB": rb, "WR": wr, "TE": te, "FLEX": flex, "DST": dst, "K": k}
if superflex:
    starters["SUPERFLEX"] = superflex

# Scoring: exact per-stat values from the league when locked, else the preset
if _use_lg and _lg and _lg.get("scoring"):
    _sc = _lg["scoring"]
    scoring_obj = E.Scoring(**{k_: v_ for k_, v_ in _sc.items()
                               if k_ in E.Scoring.__dataclass_fields__})
    # scoring_key drives ADP-format lookup; derive from reception value
    rp = _sc.get("reception", 0.5)
    scoring_key = "ppr" if rp >= 1.0 else ("half" if rp >= 0.5 else "std")
else:
    scoring_obj = E.Scoring.preset(scoring_key)

cfg = E.LeagueConfig(
    teams=int(teams), draft_slot=int(slot), rounds=int(rounds),
    scoring=scoring_obj,
    starters={p: int(v) for p, v in starters.items() if v}, bench=int(bench),
)

# connection mode
st.sidebar.markdown("---")
# defaults so league_id/season always exist regardless of which mode branch runs
# (tabs like Opponents reference league_id; Mock/Manual modes skip the ESPN block)
league_id = ss.get("league_id", "")
season = ss.get("season", 2026)
mode = st.sidebar.radio("Draft connection",
                        ["Manual entry", "ESPN live-connect", "Mock draft (practice)"])
if mode == "Mock draft (practice)":
    st.sidebar.caption("Practice against AI bots — they draft against you every "
                       "turn so you can feel the real flow. No ESPN needed.")
    mc = st.sidebar.columns(2)
    if mc[0].button("🎬 Start / Restart mock"):
        ss["_mock_action"] = "restart"
    if mc[1].button("⏭️ Bots to my pick"):
        ss["_mock_action"] = "advance"
    st.sidebar.markdown("**Mock vs. your REAL league** 😈")
    _ml_league = st.sidebar.text_input("League ID (for DNA)", key="mock_league")
    _ml_seasons = st.sidebar.text_input("Past seasons", value="2021,2022,2023,2024,2025",
                                        key="mock_seasons")
    if st.sidebar.button("🧬 Load opponents from history + start mock"):
        ss["_mock_action"] = "restart_with_dna"
    if ss.get("_mock_on"):
        st.sidebar.success(f"Mock live · overall pick {ss.current_overall} · "
                           f"your roster: {len(ss.my_roster)}")
if mode == "ESPN live-connect":
    if EC is None:
        st.sidebar.error("espn_client unavailable (requests missing).")
    else:
        # cookies first — needed to resolve names + connect
        _have_cookies = bool(ss.espn_s2 and ss.espn_swid)
        if _have_cookies:
            _src = ss.get("_cookie_source", "file")
            _srclbl = {"file": "saved on this machine", "browser": "read from your browser",
                       "manual": "entered this session",
                       "login": "captured via ESPN login"}.get(_src, "loaded")
            st.sidebar.success(f"🔓 ESPN cookies loaded ({_srclbl}) — no typing needed.")
            with st.sidebar.expander("Change / manage cookies"):
                if st.button("🔐 Re-login to ESPN (opens a window)", key="ck_relogin"):
                    with st.spinner("Opening ESPN login… sign in there."):
                        _r = ELOGIN.login()
                    if _r.get("espn_s2") and _r.get("swid"):
                        ss.espn_s2, ss.espn_swid = _r["espn_s2"], _r["swid"]
                        SEC.save_file(_r["espn_s2"], _r["swid"])
                        ss["_cookie_source"] = "login"
                        st.success("Re-logged in — session refreshed.")
                        st.rerun()
                    else:
                        st.error("Login didn't complete: "
                                 + _r.get("error", "no session captured."))
                s2 = st.text_input("espn_s2", type="password", value=ss.espn_s2, key="ck_s2")
                swid = st.text_input("SWID", type="password", value=ss.espn_swid, key="ck_swid")
                cc = st.columns(2)
                if cc[0].button("💾 Remember", key="ck_save"):
                    ss.espn_s2, ss.espn_swid = s2, swid
                    SEC.save_file(s2, swid)
                    ss["_cookie_source"] = "file"
                    st.success("Saved. Auto-loads every launch now.")
                    st.rerun()
                if cc[1].button("🗑 Forget", key="ck_forget"):
                    SEC.forget()
                    ss.espn_s2 = ss.espn_swid = ""
                    ss["_cookie_source"] = "none"
                    st.rerun()
        else:
            st.sidebar.markdown("**🔐 Log in to ESPN** — no cookies, no DevTools")
            if st.sidebar.button("🔐 Log in to ESPN (opens a window)"):
                with st.spinner("Opening ESPN login… sign in there, this grabs "
                                "your session automatically."):
                    _r = ELOGIN.login()
                if _r.get("espn_s2") and _r.get("swid"):
                    ss.espn_s2, ss.espn_swid = _r["espn_s2"], _r["swid"]
                    SEC.save_file(_r["espn_s2"], _r["swid"])
                    ss["_cookie_source"] = "login"
                    st.sidebar.success("Logged in — session saved. Auto-loads from now on.")
                    st.rerun()
                else:
                    st.sidebar.error("Login didn't complete: "
                                     + _r.get("error", "no session captured."))
            st.sidebar.caption("Opens a real ESPN login window. Sign in normally "
                               "(password/MFA on ESPN's own page — we never see it); "
                               "the app captures your session cookies when you're in.")
            with st.sidebar.expander("…or paste cookies manually"):
                s2 = st.text_input("espn_s2", type="password", value=ss.espn_s2, key="ck_s2m")
                swid = st.text_input("SWID", type="password", value=ss.espn_swid, key="ck_swidm")
                cc = st.columns(2)
                if cc[0].button("💾 Remember", key="ck_savem"):
                    ss.espn_s2, ss.espn_swid = s2, swid
                    SEC.save_file(s2, swid)
                    ss["_cookie_source"] = "file"
                    st.rerun()
                if cc[1].button("🌐 From browser", key="ck_browserm"):
                    _bs2, _bswid, _bmsg = SEC.read_from_browser()
                    if _bs2 or _bswid:
                        ss.espn_s2, ss.espn_swid = _bs2, _bswid
                        SEC.save_file(_bs2, _bswid)
                        ss["_cookie_source"] = "browser"
                        st.success(_bmsg + " Saved.")
                        st.rerun()
                    else:
                        st.info(_bmsg)
                if s2:
                    ss.espn_s2 = s2
                if swid:
                    ss.espn_swid = swid

        # ---------- AUTO-DISCOVER: pull every league on the account ----------
        if st.sidebar.button("🔎 Auto-discover my leagues"):
            if not ss.espn_swid:
                st.sidebar.warning("Paste your SWID (and espn_s2) above first.")
            else:
                with st.spinner("Asking ESPN for your leagues…"):
                    res = EC.discover_leagues(ss.espn_s2, ss.espn_swid)
                if res.get("ok") and res.get("leagues"):
                    SL.bulk_upsert(res["leagues"])
                    ss.espn_status = res["message"]
                    st.sidebar.success(res["message"] + " Added to Saved leagues.")
                    st.rerun()
                else:
                    st.sidebar.error(res.get("message", "Discovery failed."))

        # ---------- SAVED LEAGUES: preload IDs, pick one at draft time ----------
        _saved = SL.load()
        st.sidebar.markdown("**📁 Saved leagues**")
        _default_season = 2026
        if _saved:
            _opts = ["— pick a saved league —"] + [SL.display_label(e) for e in _saved]
            _sel = st.sidebar.selectbox("Select league", _opts,
                                        label_visibility="collapsed")
            _picked = None
            if _sel != _opts[0]:
                _picked = _saved[_opts.index(_sel) - 1]
            if _picked:
                league_id = str(_picked["league_id"])
                season = int(_picked.get("season", _default_season))
                if _picked.get("resolved") and _picked.get("my_team_name"):
                    st.sidebar.caption(f'🏈 Your team: **{_picked["my_team_name"]}**'
                                       f' · {_picked.get("team_count","?")}-team')
            else:
                league_id, season = "", _default_season
        else:
            st.sidebar.caption("No saved leagues yet — add your league IDs below "
                               "so you can pick them fast when drafts go live.")
            league_id, season = "", _default_season

        with st.sidebar.expander("➕ Add / manage saved leagues"):
            _new_id = st.text_input("League ID to save", key="sl_new_id")
            _new_season = st.number_input("Season", 2020, 2030, _default_season,
                                          key="sl_new_season")
            _new_label = st.text_input("Nickname (optional)", key="sl_new_label")
            if st.button("Save league ID", key="sl_add"):
                if _new_id.strip().isdigit():
                    SL.add(int(_new_id), int(_new_season), _new_label.strip())
                    st.success(f"Saved league {_new_id}. Now hit "
                               "'Resolve names' to pull its ESPN name + your team.")
                    st.rerun()
                else:
                    st.warning("Enter a numeric league ID.")
            if _saved:
                st.markdown("---")
                if st.button("🔄 Resolve names for all (needs cookies)",
                             key="sl_resolve"):
                    if not (ss.espn_s2 and ss.espn_swid):
                        st.warning("Add espn_s2 + SWID above first — private "
                                   "leagues need them to read names.")
                    else:
                        _ok, _fail = 0, 0
                        for e in _saved:
                            try:
                                _cli = EC.EspnClient(int(e["league_id"]),
                                                     int(e.get("season", _default_season)),
                                                     ss.espn_s2, ss.espn_swid)
                                SL.update_resolved(_cli.league_profile())
                                _ok += 1
                            except Exception:  # noqa: BLE001
                                _fail += 1
                        st.success(f"Resolved {_ok} league(s)"
                                   + (f", {_fail} failed" if _fail else "") + ".")
                        st.rerun()
                _rm = st.selectbox("Remove a league",
                                   ["—"] + [SL.display_label(e) for e in _saved],
                                   key="sl_rm")
                if _rm != "—" and st.button("Delete", key="sl_del"):
                    _t = _saved[[SL.display_label(e) for e in _saved].index(_rm)]
                    SL.remove(_t["league_id"], _t.get("season", _default_season))
                    st.rerun()

        st.sidebar.markdown("**Connect to a draft**")
        league_id = st.sidebar.text_input("ESPN league ID",
                                          value=str(league_id or ""),
                                          key="espn_league_id")
        season = st.sidebar.number_input("Season", 2020, 2030, int(season))
        _dna_seasons_txt = st.sidebar.text_input(
            "Learn opponent DNA from seasons", value="2021,2022,2023,2024,2025",
            help="Past seasons of THIS league to learn each manager's real "
                 "draft tendencies (RB-early, WR-zealot, etc). Blank = skip.")
        with st.sidebar.expander("How to get espn_s2 / SWID"):
            st.write("Log into ESPN in your browser → DevTools (F12) → "
                     "Application → Cookies → espn.com → copy `espn_s2` and `SWID`. "
                     "Once connected, cookies are remembered — to switch leagues "
                     "just pick a saved league (or change the League ID) and hit "
                     "Connect again.")
        if st.sidebar.button("Connect"):
            if not str(league_id).strip().isdigit():
                ss.espn_status = ("Enter a numeric ESPN League ID first "
                                  "(e.g. 630798), then hit Connect.")
            try:
                if not str(league_id).strip().isdigit():
                    raise ValueError("skip")   # guarded above; do nothing
                cli = EC.EspnClient(int(league_id), int(season),
                                    ss.espn_s2, ss.espn_swid)
                ok, msg = cli.verify()
                ss.espn = cli if ok else None
                ss.espn_status = msg
                if ok:
                    # cache league profile (name + my team) for later weeks
                    try:
                        SL.update_resolved(cli.league_profile())
                    except Exception:  # noqa: BLE001
                        pass
                    # real team + owner names per draft slot (replaces 'slot N')
                    try:
                        ss["_slot_labels"] = cli.slot_labels()
                    except Exception:  # noqa: BLE001
                        ss["_slot_labels"] = {}
                    # league settings (teams/scoring/lineup) -> engine defaults
                    try:
                        ss["_league_settings"] = cli.settings_profile()
                    except Exception:  # noqa: BLE001
                        ss["_league_settings"] = None
                    # ---- learn opponent DNA from past drafts (auto on connect) ----
                    ss["_dna_note"] = ""
                    try:
                        yrs = [int(y) for y in _dna_seasons_txt.replace(" ", "").split(",")
                               if y.strip().isdigit()]
                    except Exception:  # noqa: BLE001
                        yrs = []
                    if yrs:
                        with st.spinner(f"Learning opponent DNA from {yrs}…"):
                            try:
                                drafts = LH.pull_past_drafts(int(league_id), yrs,
                                                             ss.espn_s2, ss.espn_swid)
                                mgr_dna = LH.learn_dna_by_manager(drafts) if drafts else {}
                                s2o = LH.current_slot_to_owner(int(league_id), int(season),
                                                               ss.espn_s2, ss.espn_swid)
                                ss["_mgr_dna"] = mgr_dna
                                ss["_slot_to_owner"] = s2o
                                # who DRAFTED each player, by year (not EOY ownership)
                                ss["_draft_hist"] = (LH.player_draft_history(drafts)
                                                     if drafts else {})
                                _learned = sum(1 for d in mgr_dna.values()
                                               if d["tendencies"] != ["ADP-robot"])
                                ss["_dna_note"] = (
                                    f"🧬 Learned DNA for {len(mgr_dna)} managers "
                                    f"({_learned} with a real lean) across "
                                    f"{len(drafts)} past draft(s).")
                            except Exception as ex:  # noqa: BLE001
                                ss["_mgr_dna"] = {}
                                ss["_slot_to_owner"] = {}
                                ss["_dna_note"] = f"DNA learn failed: {ex}"
            except Exception as ex:  # noqa: BLE001
                if str(ex) != "skip":
                    ss.espn = None
                    ss.espn_status = f"Connect failed: {ex}"
        if ss.espn_status:
            (st.sidebar.success if ss.espn else st.sidebar.error)(ss.espn_status)
        if ss.get("_dna_note"):
            st.sidebar.caption(ss["_dna_note"])


# --------------------------------------------------------------------------- data
@st.cache_data(show_spinner=False)
def _pool():
    return P.load_players(prefer_live=True)


pool = _pool()
name_to_raw = {p.name: p for p in pool}


def _snapshot():
    import copy
    return {
        "drafted": set(ss.drafted), "my_roster": list(ss.my_roster),
        "current_overall": ss.current_overall,
        "pick_log": list(ss.pick_log),
        "team_rosters": copy.deepcopy(ss.team_rosters),
    }


def _record_pick(name, position, mine: bool):
    """Log a pick, update rosters, push undo snapshot, and note any regret."""
    ss.undo_stack.append(_snapshot())
    ss.undo_stack[:] = ss.undo_stack[-30:]
    ov = int(ss.current_overall)
    slot = O._snake_slot(ov, int(teams))
    ss.pick_log.append((ov, name, position, slot))
    ss.team_rosters.setdefault(slot, []).append(name)
    ss.drafted.add(name)
    if mine:
        ss.my_roster.append((name, position))
        ss["_last_trash"] = CO.trash_talk(name)
        # SHADOW LEDGER: log what Shredder recommended here vs what I took
        try:
            _rnd = (ov - 1) // int(teams) + 1
            _top = ss.get("_top_rec")   # stashed by the board each render
            if _top:
                SLG.record_pick(
                    overall=ov, round_=_rnd,
                    shredder_pick=_top.get("name"), shredder_pos=_top.get("position"),
                    shredder_adp=_top.get("adp"),
                    shredder_reason=" · ".join(_top.get("badges", [])[:3]),
                    actual_pick=name, actual_pos=position,
                    league_id=(int(league_id) if str(league_id).isdigit() else None),
                    season=int(season) if str(season).isdigit() else None)
        except Exception:  # noqa: BLE001 — ledger must never block a pick
            pass
    else:
        opps.note_pick(slot, position)
        # regret: log a player I passed who just went to someone else
        ss.regret.append((name, ov, f"slot {slot}"))
        ss.regret[:] = ss.regret[-50:]
    ss.current_overall = ov + 1
    # in mock mode, after MY pick the bots immediately draft to my next turn
    if mine and ss.get("_mock_on"):
        _r = MOCK.bots_pick_until_me(pool, cfg, ss.drafted, ss.team_rosters,
                                     int(ss.current_overall), opponents=opps,
                                     pick_log=ss.pick_log)
        ss.current_overall = _r["now_overall"]


def _undo():
    if not ss.undo_stack:
        return False
    snap = ss.undo_stack.pop()
    ss.drafted = snap["drafted"]
    ss.my_roster = snap["my_roster"]
    ss.current_overall = snap["current_overall"]
    ss.pick_log = snap["pick_log"]
    ss.team_rosters = snap["team_rosters"]
    return True


# --------------------------------------------------------------------------- ESPN sync
def _sync_espn():
    if not ss.espn:
        return
    try:
        state = ss.espn.draft_state()
    except Exception as ex:  # noqa: BLE001
        st.warning(f"ESPN poll failed (using last state): {ex}")
        return
    # map ESPN picks -> our drafted set via NORMALIZED name against the pool,
    # so suffix/punctuation differences (Jr./III, D.J. vs DJ) never miss.
    try:
        import live_feed as _lf
        _norm = _lf._norm
    except Exception:
        def _norm(s):  # fallback: lowercase alnum
            import re as _re
            return _re.sub(r"[^a-z0-9]", "", (s or "").lower())

    pool_by_norm = {_norm(p.name): p.name for p in pool}
    my_slot = int(slot)
    matched, unmatched = 0, []
    for pk in state.picks:
        nm = pk.player_name
        if not nm:
            continue
        canon = pool_by_norm.get(_norm(nm), nm)   # map to our pool name if we can
        ss.drafted.add(canon)
        matched += 1
        # is this MY pick? (ESPN draft_slot matches my configured slot)
        if getattr(pk, "draft_slot", None) == my_slot and \
           canon not in {n for n, _ in ss.my_roster}:
            pos = pk.position or (name_to_raw.get(canon).position
                                  if name_to_raw.get(canon) else "")
            if pos:
                ss.my_roster.append((canon, pos))
        if _norm(nm) not in pool_by_norm:
            unmatched.append(nm)
    ss.current_overall = len(state.picks) + 1
    ss.espn_on_clock = getattr(state, "on_the_clock_team", None)
    ss.espn_sync_note = (f"Synced {matched} picks."
                         + (f" ⚠️ {len(unmatched)} not in pool: "
                            + ", ".join(unmatched[:5]) if unmatched else ""))


# --------------------------------------------------------------------------- header
import base64 as _b64
_icon_uri = ""
_icon128 = _os.path.join(_os.path.dirname(__file__), "assets", "shredder_icon_128.png")
if _os.path.exists(_icon128):
    with open(_icon128, "rb") as _f:
        _icon_uri = "data:image/png;base64," + _b64.b64encode(_f.read()).decode()
_hero_icon = (f'<img src="{_icon_uri}" width="54" height="54" '
              f'style="border-radius:8px;margin-right:14px;vertical-align:middle;'
              f'border:1px solid var(--line);">' if _icon_uri else "")
st.markdown(
    '<div class="hero" style="display:flex;align-items:center;">'
    f'{_hero_icon}'
    '<div><h1 style="margin:0;">PROJECT SHREDDER</h1>'
    '<div class="sub">// live-draft copilot · VORP + edge engine · opponent DNA · '
    'prophecy · draft with soul — shred the board</div></div></div>',
    unsafe_allow_html=True)
picks = cfg.my_overall_picks()
gaps = cfg.gaps_between_my_picks()
next_pick = next((p for p in picks if p >= ss.current_overall), None)
picks_until = (next_pick - ss.current_overall) if next_pick else 0

hcols = st.columns(4)
hcols[0].metric("On the clock (overall)", ss.current_overall)
hcols[1].metric("Your next pick", next_pick if next_pick else "—",
                f"in {picks_until}" if picks_until else "NOW")
hcols[2].metric("Scoring", scoring_label.split()[0])
hcols[3].metric("Your picks", ", ".join(map(str, picks[:6])) + "…")

if ss.espn:
    scol = st.columns([1, 1, 4])
    if scol[0].button("🔄 Sync now"):
        _sync_espn()
        st.rerun()
    auto = scol[1].toggle("Auto-poll (4s)", value=ss.get("espn_auto", False))
    ss.espn_auto = auto
    if ss.get("espn_sync_note"):
        scol[2].caption(ss.espn_sync_note)
    if auto:
        _sync_espn()
        try:
            from streamlit.runtime.scriptrunner import add_script_run_ctx  # noqa
        except Exception:
            pass
        st.caption("⏱️ Auto-polling ESPN every ~4s — board updates itself.")
        import time as _t
        _t.sleep(4)
        st.rerun()


tab_board, tab_stack, tab_opp, tab_proph, tab_roster, tab_sim, tab_week, tab_waiver, tab_rank, tab_ledger, tab_live, tab_value, tab_lab, tab_picks = st.tabs(
    ["🎯 Board", "🔗 Stack Lab", "🕵️ Opponents", "🔮 Prophecy", "🧢 My Roster",
     "🧪 Strategy Sim", "📅 Weekly Lineup", "📡 Waiver Wire", "📊 Rankings",
     "📒 Shadow Ledger", "🎲 Live & Lines", "📈 Value History", "🧬 Roster Lab", "✍️ Enter Picks"])

# build/refresh opponents to match current league size + slot (keep tendencies)
def _ensure_opponents():
    opp = ss.opponents
    if (opp is None or opp.teams != int(teams) or opp.my_slot != int(slot)):
        new = O.LeagueOpponents.default(int(teams), int(slot))
        if opp is not None:  # carry over any tendencies already set
            for s_, prof in opp.profiles.items():
                if s_ in new.profiles:
                    new.profiles[s_].tendencies = prof.tendencies
                    new.profiles[s_].rookie_averse = prof.rookie_averse
                    new.profiles[s_].favorite_team = prof.favorite_team
        ss.opponents = new
    return ss.opponents


opps = _ensure_opponents()

# apply learned manager DNA (real tendencies) to opponent profiles by seat
_mgr_dna = ss.get("_mgr_dna") or {}
_s2o = ss.get("_slot_to_owner") or {}
if _mgr_dna and _s2o:
    try:
        LH.apply_manager_dna(opps, _mgr_dna, _s2o)
    except Exception:  # noqa: BLE001
        pass

# stamp real ESPN team+owner names onto opponent profiles (replaces 'slot N')
_slot_labels = ss.get("_slot_labels") or {}
if _slot_labels:
    for _s, _info in _slot_labels.items():
        _prof = opps.profiles.get(int(_s))
        if _prof is not None and _info.get("label"):
            _prof.name = _info["label"]

# deferred mock-draft actions (set as intents in the sidebar; run here where
# pool + opps exist)
_ma = ss.pop("_mock_action", None)
if _ma == "restart":
    ss.drafted = set(); ss.my_roster = []; ss.team_rosters = {}
    ss.pick_log = []; ss.undo_stack = []; ss.regret = []
    ss.current_overall = 1; ss["_mock_on"] = True
    _r = MOCK.bots_pick_until_me(pool, cfg, ss.drafted, ss.team_rosters,
                                 int(ss.current_overall), opponents=opps,
                                 pick_log=ss.pick_log)
    ss.current_overall = _r["now_overall"]
    st.rerun()
elif _ma == "advance":
    _r = MOCK.bots_pick_until_me(pool, cfg, ss.drafted, ss.team_rosters,
                                 int(ss.current_overall), opponents=opps,
                                 pick_log=ss.pick_log)
    ss.current_overall = _r["now_overall"]
    st.rerun()
elif _ma == "restart_with_dna":
    # 1) learn each manager's DNA from real past drafts into opps profiles
    try:
        _lid = ss.get("mock_league", "")
        _seas = [int(x) for x in ss.get("mock_seasons", "2021,2022,2023,2024,2025").split(",")
                 if x.strip()]
        _drafts = LH.pull_past_drafts(int(_lid), _seas,
                                      ss.get("espn_s2", ""), ss.get("espn_swid", ""))
        if _drafts:
            _mgr = LH.learn_dna_by_manager(_drafts)
            ss["_draft_hist"] = LH.player_draft_history(_drafts)
            _so = LH.current_slot_to_owner(int(_lid), 2026,
                                           ss.get("espn_s2", ""), ss.get("espn_swid", ""))
            if _so:
                LH.apply_manager_dna(opps, _mgr, _so)
            else:
                LH.apply_dna(opps, LH.learn_dna(_drafts))
            ss["_dna"] = {m: d["dossier"] for m, d in _mgr.items()}
            ss["_mock_dna_note"] = (f"Loaded {len(_mgr)} real managers' DNA into "
                                    f"the mock bots.")
        else:
            ss["_mock_dna_note"] = ("No history found — mock uses default bot "
                                    "tendencies (set styles in Opponents tab).")
    except Exception as ex:  # noqa: BLE001
        ss["_mock_dna_note"] = f"DNA load failed ({ex}); using default bots."
    # 2) start the mock against those opponents
    ss.drafted = set(); ss.my_roster = []; ss.team_rosters = {}
    ss.pick_log = []; ss.undo_stack = []; ss.regret = []
    ss.current_overall = 1; ss["_mock_on"] = True
    _r = MOCK.bots_pick_until_me(pool, cfg, ss.drafted, ss.team_rosters,
                                 int(ss.current_overall), opponents=opps,
                                 pick_log=ss.pick_log)
    ss.current_overall = _r["now_overall"]
    st.rerun()


# --------------------------------------------------------------------------- board
def _render_board():
    roster = X.Roster(players=list(ss.my_roster))
    recs = X.recommend(pool, cfg, roster, set(ss.drafted),
                       current_overall=int(ss.current_overall),
                       scoring_key=scoring_key, top_n=40, opponents=opps,
                       prefer_floor=prefer_floor)
    # stash the top rec so _record_pick can log Shredder's shadow pick
    if recs:
        _b = recs[0]
        ss["_top_rec"] = {"name": _b.name, "position": _b.position,
                          "adp": _b.adp, "badges": list(_b.badges)}
    else:
        ss["_top_rec"] = None

    # tier-cliff alarm banner — only when a NEEDED position is about to cliff
    try:
        _bcliffs = [c for c in RLAB.tier_cliff(pool, set(ss.drafted),
                    list(ss.my_roster), scoring_key) if c.urgency == "now"]
    except Exception:
        _bcliffs = []
    if _bcliffs:
        st.error("🚨 CLIFF: " + " · ".join(
            f"{c.position} ({c.remaining_tier} left, need {c.need})" for c in _bcliffs)
            + " — draft this position before it falls off.")

    # ======================= WHOSE TURN IS IT? — dual-view switch =======================
    _ov = int(ss.current_overall)
    _on_clock_slot = O._snake_slot(_ov, int(teams))
    _my_slot = int(slot)
    _is_my_turn = (_on_clock_slot == _my_slot)
    # how many picks until my next turn?
    _picks_until_me = 0
    if not _is_my_turn:
        _scan = _ov
        while O._snake_slot(_scan, int(teams)) != _my_slot and _scan < _ov + int(teams) * 2:
            _scan += 1
        _picks_until_me = _scan - _ov
    _on_clock_name = (opps.profiles[_on_clock_slot].name
                      if (opps and _on_clock_slot in opps.profiles
                          and opps.profiles[_on_clock_slot].name) else None)
    _rnd_now = (_ov - 1) // int(teams) + 1

    # view mode: Auto follows whose turn it is; user can pin either view
    _view_choice = st.radio(
        "View", ["🤖 Auto", "🎯 My Pick", "🕵️ Opponent Watch"],
        horizontal=True, label_visibility="collapsed", key="board_view")
    if _view_choice == "🎯 My Pick":
        _my_pick_view = True
    elif _view_choice == "🕵️ Opponent Watch":
        _my_pick_view = False
    else:  # Auto
        _my_pick_view = _is_my_turn

    # the on-the-clock banner — always visible so you never lose the thread
    if _is_my_turn:
        st.markdown(
            f'<div style="background:linear-gradient(100deg,#141410,#1a1a0f);'
            f'border:1px solid var(--accent);border-left:3px solid var(--accent);'
            f'border-radius:6px;padding:10px 16px;margin-bottom:10px;">'
            f'<span style="font:700 13px \'Space Grotesk\',sans-serif;'
            f'text-transform:uppercase;letter-spacing:.6px;color:var(--accent);">'
            f'⏱ YOU\'RE ON THE CLOCK</span> '
            f'<span class="pmeta">· overall {_ov} · round {_rnd_now} · '
            f'slot {_my_slot}</span></div>',
            unsafe_allow_html=True)
    else:
        _who = _on_clock_name or f"slot {_on_clock_slot}"
        st.markdown(
            f'<div style="background:var(--panel);border:1px solid var(--line);'
            f'border-left:3px solid var(--accent2);border-radius:6px;'
            f'padding:10px 16px;margin-bottom:10px;">'
            f'<span style="font:700 13px \'Space Grotesk\',sans-serif;'
            f'text-transform:uppercase;letter-spacing:.6px;color:var(--accent2);">'
            f'🕵️ ON THE CLOCK: {_who}</span> '
            f'<span class="pmeta">· overall {_ov} · your next pick in '
            f'{_picks_until_me} pick{"s" if _picks_until_me != 1 else ""}</span></div>',
            unsafe_allow_html=True)

    if _my_pick_view:
        _render_my_pick_view(pool, cfg, recs, opps, scoring_key, prefer_floor,
                             slot, teams)
    else:
        _render_opponent_view(pool, cfg, recs, opps, scoring_key, slot, teams,
                              _on_clock_slot, _on_clock_name, _picks_until_me)


def _render_my_pick_view(pool, cfg, recs, opps, scoring_key, prefer_floor,
                         slot, teams):
    # ============================ COPILOT COMMAND CENTER ============================
    if ss.get("_mock_on") and ss.get("_mock_dna_note"):
        st.info("🧬 " + ss["_mock_dna_note"])
    if recs:
        # Best pick right now — the hero
        best = recs[0]
        st.markdown(
            f'<div style="background:linear-gradient(100deg,#0d2a2e,#12203a);'
            f'border:1px solid #22d3ee;border-radius:16px;padding:16px 20px;'
            f'margin-bottom:10px;box-shadow:0 0 26px rgba(34,211,238,.22);">'
            f'<span style="font:800 13px Inter;color:#22d3ee;letter-spacing:1px;">'
            f'⚡ TAKE NOW</span><br>'
            f'<span style="font:800 24px Inter;color:#e8eef7;">{best.name}</span> '
            f'<span class="pill pos-{best.position}">{best.position}</span> '
            f'<span class="pmeta">{best.team}</span><br>'
            f'<span style="color:#9fe8f2;font-size:13px;">'
            + " · ".join(best.badges[:4]) + '</span></div>',
            unsafe_allow_html=True)

    # run detector + nemesis + tilt banners
    _recent_pos = [p[2] for p in ss.pick_log]
    _run = CO.run_detector(_recent_pos)
    if _run:
        st.warning(_run)
    _pos_of = {n: (name_to_raw[n].position if n in name_to_raw else "?")
               for lst in ss.team_rosters.values() for n in lst}
    _nem = CO.nemesis(ss.team_rosters, int(slot), _pos_of)
    if _nem:
        st.info(_nem)
    # tilt: was the last pick a reach vs ADP?
    if ss.pick_log:
        _lov, _lname, _lpos, _lslot = ss.pick_log[-1]
        _lraw = name_to_raw.get(_lname)
        _ladp = P.adp_for(_lraw, scoring_key) if _lraw else None
        _reach = (_ladp is not None and _lov < _ladp - 10)
        _tilt = CO.tilt(_lslot if _lslot != int(slot) else None, _reach)
        if _tilt:
            st.warning(_tilt)
    # villain narration for the current round
    _rnd = (int(ss.current_overall) - 1) // int(teams) + 1
    _mylast = ss.my_roster[-1][0] if ss.my_roster else None
    st.markdown(f'<div class="pmeta" style="font-style:italic;color:#a78bfa;">'
                f'🎭 {CO.villain_line(_rnd, _mylast)}</div>', unsafe_allow_html=True)

    # 🎡 WHEEL PLAY — grab-now-vs-wait across your snake turn
    _slot_names = {s_: (opps.profiles[s_].name or f"slot {s_}")
                   for s_ in opps.profiles}
    _pair = WP.best_pair(pool, cfg, set(ss.drafted), int(ss.current_overall),
                         opponents=opps, scoring_key=scoring_key)
    if _pair and _pair.get("take_now"):
        tn = _pair["take_now"]
        tw = _pair.get("then_wheel")
        wheel_txt = (f'<b style="color:#e8eef7;">TAKE NOW:</b> {tn[0]} ({tn[1]})'
                     + (f' &nbsp;→&nbsp; <b style="color:#e8eef7;">WHEEL BACK:</b> '
                        f'{tw[0]} ({tw[1]})' if tw else ''))
        st.markdown(
            f'<div style="background:linear-gradient(100deg,#20122e,#101e33);'
            f'border:1px solid #22d3ee;border-radius:12px;padding:10px 14px;'
            f'margin-bottom:8px;">'
            f'<span style="font:800 12px Inter;color:#22d3ee;letter-spacing:1px;">'
            f'🎡 WHEEL PLAY</span><br>{wheel_txt}<br>'
            f'<span class="pmeta">{_pair["logic"]}</span></div>',
            unsafe_allow_html=True)

    # ➕ TD PACKAGE — same-team QB+RB combos projected for 40+ combined TDs
    _combos = WP.td_combos(pool, scoring_key, min_tds=40.0)
    _combos = [c for c in _combos
               if c["qb"] not in ss.drafted or c["rb"] not in ss.drafted]
    if _combos:
        _rows = ""
        for c in _combos[:6]:
            _qb_gone = "✓" if c["qb"] in ss.drafted else ""
            _rb_gone = "✓" if c["rb"] in ss.drafted else ""
            _rows += (
                f'<div style="margin:4px 0;">'
                f'<span style="color:#111;background:var(--accent);font-weight:800;'
                f'border-radius:3px;padding:0 5px;">➕</span> '
                f'<b style="color:var(--txt);">{c["qb"]}</b>{_qb_gone} '
                f'<span class="pmeta">+ </span>'
                f'<b style="color:var(--txt);">{c["rb"]}</b>{_rb_gone} '
                f'<span class="pmeta">({c["team"]}) — {c["combined"]:.0f} combined TDs '
                f'(QB {c["qb_td"]:.0f} / RB {c["rb_td"]:.0f})</span></div>')
        st.markdown(
            f'<div style="background:linear-gradient(100deg,#0d1a10,#12140a);'
            f'border:1px solid var(--accent);border-left:3px solid var(--accent);'
            f'border-radius:6px;padding:10px 14px;margin-bottom:8px;">'
            f'<span style="font:700 12px \'Space Grotesk\',sans-serif;'
            f'color:var(--accent);text-transform:uppercase;letter-spacing:.5px;">'
            f'➕ TD PACKAGE — 40+ combined-TD QB/RB stacks</span><br>{_rows}'
            f'<span class="pmeta">Rostering both concentrates a scoring-machine '
            f'offense — draft them as a package.</span></div>',
            unsafe_allow_html=True)

    # championship equity + ghost draft (Monte Carlo) — computed on demand
    ecol = st.columns([1, 1, 1, 1])
    if ecol[0].button("🎲 Run Monte Carlo", help="Simulate the rest of the draft"):
        with st.spinner("Simulating 300 drafts…"):
            sim = SIMU.simulate(pool, cfg, set(ss.drafted), list(ss.my_roster),
                                int(ss.current_overall), opponents=opps, n=300)
            ss["_sim"] = sim
    _sim = ss.get("_sim")
    if _sim:
        ecol[1].metric("🏆 Title equity", f"{int(_sim.championship_equity*100)}%")
        ecol[2].metric("Your proj", f"{_sim.your_proj_points:.0f}")
        delta = _sim.your_proj_points - _sim.ghost_points
        ecol[3].metric("👻 vs Ghost", f"{'+' if delta>=0 else ''}{delta:.0f}",
                       help="You minus the pure-VORP shadow AI")
    # undo + chaos + soul + team name
    ucol = st.columns([1, 1, 1.2, 1.8])
    if ucol[0].button("↩️ Undo last pick"):
        if _undo():
            st.toast("Reverted last pick (local only — not ESPN).")
            st.rerun()
    if ucol[1].button("🌀 Chaos pick") and recs:
        _cp = CO.chaos_pick(recs)
        if _cp:
            st.toast(f"CHAOS: {_cp.name} — highest-ceiling swing.")
    if ucol[2].button("🔥 Draft with Soul",
                      help="A player the projections don't hype yet, but every "
                           "underlying signal says will excel."):
        ss["_soul"] = SOUL.find_soul(pool, cfg, scoring_key, set(ss.drafted))
        if ss["_soul"] is None:
            st.toast("No soul pick on the board right now — signals are quiet.")
    _tn = CO.team_name([p for _, p in ss.my_roster])
    ucol[3].markdown(f'<div class="pmeta">Your squad: '
                     f'<b style="color:var(--accent2);">{_tn}</b></div>',
                     unsafe_allow_html=True)
    # ===============================================================================

    _soul = ss.get("_soul")
    if _soul and _soul.name not in set(ss.drafted):
        _sig_chips = " ".join(
            f'<span class="bdg bdg-stack">{s}</span>' for s in _soul.signals[:4])
        st.markdown(
            f'<div style="background:linear-gradient(100deg,#170d13,#1a0f16);'
            f'border:1px solid var(--accent2);border-left:3px solid var(--accent2);'
            f'border-radius:6px;padding:14px 18px;margin-bottom:12px;'
            f'box-shadow:0 0 24px rgba(255,46,136,.20);">'
            f'<span style="font:700 14px \'Space Grotesk\',sans-serif;'
            f'text-transform:uppercase;letter-spacing:.5px;color:var(--accent2);">'
            f'🔥 Draft with Soul</span><br>'
            f'<span class="pname">{_soul.name}</span> '
            f'<span class="pill pos-{_soul.position}">{_soul.position}</span> '
            f'<span class="pmeta">{_soul.team} · ADP '
            f'{_soul.adp if _soul.adp else "undrafted"} · soul {_soul.soul_score}</span><br>'
            f'<div style="margin:6px 0 4px;">{_sig_chips}</div>'
            f'<span style="color:#f5b6d0;font-size:13px;">{_soul.thesis}</span></div>',
            unsafe_allow_html=True)
    elif _soul and _soul.name in set(ss.drafted):
        ss["_soul"] = None      # they got drafted — clear the stale card

    _dh = DH.recommend_if_right(pool, cfg, list(ss.my_roster), set(ss.drafted),
                                int(ss.current_overall), scoring_key)
    if _dh:
        st.markdown(
            f'<div style="background:linear-gradient(100deg,#2a1836,#3a1f2e);'
            f'border:1px solid #a78bfa;border-radius:14px;padding:14px 18px;'
            f'margin-bottom:12px;box-shadow:0 0 24px rgba(167,139,250,.25);">'
            f'<span style="font:800 15px Inter;color:#fcd34d;">🐴 DARK HORSE — '
            f'the time is right.</span><br>'
            f'<span style="font:700 18px Inter;color:#e8eef7;">{_dh.name}</span> '
            f'<span class="pill pos-{_dh.position}">{_dh.position}</span> '
            f'<span class="pmeta">{_dh.team} · ADP '
            f'{_dh.adp if _dh.adp else "undrafted"} · ceiling {_dh.ceiling_score}</span><br>'
            f'<span style="color:#c9b6f5;font-size:13px;">{_dh.thesis}</span></div>',
            unsafe_allow_html=True)
    if not recs:
        st.info("No players available — load data or reset the board.")
    else:
        fcol1, fcol2 = st.columns([3, 1])
        pos_filter = fcol1.multiselect("Filter position",
                                       ["QB", "RB", "WR", "TE", "K", "DST"])
        shown = [r for r in recs if not pos_filter or r.position in pos_filter]
        fcol2.metric("Best available", len(shown))
        _combo_names = WP.combo_players(pool, scoring_key, min_tds=40.0)
        for idx, r in enumerate(shown):
            with st.container(border=True):
                top = " ⭐" if idx == 0 else ""
                _plus = (' <span title="Part of a 40+ combined-TD QB/RB package" '
                         'style="color:#111;background:var(--accent);font-weight:800;'
                         'border-radius:3px;padding:0 4px;">➕</span>'
                         if r.name in _combo_names else "")
                cols = st.columns([3.2, 0.9, 0.9, 0.9, 4.2])
                adp_txt = r.adp if r.adp else "—"
                vva = (f'<span class="pmeta"> · vs ADP '
                       f'{"+" if (r.value_vs_adp or 0) >= 0 else ""}'
                       f'{r.value_vs_adp}</span>') if r.value_vs_adp is not None else ""
                surv = (f'<span class="pmeta"> · {int(r.survival*100)}% to next</span>'
                        if r.survival is not None else "")
                _injcss = {"O": "inj-out", "IR": "inj-out", "PUP": "inj-out",
                           "SUS": "inj-out", "DNR": "inj-out", "D": "inj-doubt",
                           "Q": "inj-quest"}.get(r.injury_chip, "inj-quest")
                inj_chip = (f'<span class="inj {_injcss}">{r.injury_chip}</span>'
                            if r.injury_chip else "")
                cols[0].markdown(
                    f'<span class="pill pos-{r.position}">{r.position}</span> '
                    f'<span class="pname">{r.name}{top}</span>{_plus}{inj_chip}<br>'
                    f'<span class="pmeta">{r.team}{vva}{surv}</span>',
                    unsafe_allow_html=True)
                cols[1].metric("VORP", r.vorp)
                cols[2].metric("Tier", f"T{r.tier}")
                cols[3].metric("ADP", adp_txt)
                cols[4].markdown(_badges_html(r.badges), unsafe_allow_html=True)
                if r.injury_note:
                    cols[4].markdown(f'<span class="pmeta">🏥 {r.injury_note}</span>',
                                     unsafe_allow_html=True)
                _sit = SIT.context_for(r.name, r.position, r.team)
                if _sit["notes"]:
                    _tdcol = {"protected": "var(--good)", "capped": "var(--warn)"}.get(
                        _sit["td_tag"], "var(--dim)")
                    cols[4].markdown(
                        "".join(f'<span class="pmeta" style="color:{_tdcol};">🧭 {n}</span><br>'
                                for n in _sit["notes"]),
                        unsafe_allow_html=True)
                # 🎯 DRAFT LOYALTY — who DRAFTED this player in past years (not
                # end-of-year ownership). Powered by player_draft_history.
                _dh = ss.get("_draft_hist") or {}
                _hist = LH.lookup_player_history(_dh, r.name) if _dh else None
                if _hist and _hist.get("events"):
                    _by = _hist["by_manager"]           # {manager_name: count}
                    _parts = []
                    for _mgr_nm, _c in list(_by.items())[:3]:
                        _yrs = sorted({e["season"] for e in _hist["events"]
                                       if e["manager_name"] == _mgr_nm and e["season"]},
                                      reverse=True)
                        _yrtxt = ", ".join(str(y) for y in _yrs)
                        _loyal = " 🔁" if _c >= 2 else ""
                        _parts.append(f"{_mgr_nm} ({_yrtxt}){_loyal}")
                    cols[4].markdown(
                        '<span class="pmeta" style="color:#a78bfa;">🎯 Drafted by: '
                        + "; ".join(_parts) + '</span>',
                        unsafe_allow_html=True)
                if r.position in ("RB", "WR", "TE", "QB") and not SIT.has_context(r.name):
                    if cols[4].button("🔎 Deep read", key=f"deep_{r.name}",
                                      help="Live-fetch this player's current team + latest news from ESPN"):
                        with st.spinner(f"Reading {r.name}…"):
                            res = SITL.deep_read(r.name, r.position, r.team)
                        if not res["ok"]:
                            st.toast(f"{r.name}: {res['note']}", icon="⚠️")
                        st.rerun()
                bcols = cols[4].columns(2)
                if bcols[0].button("✓ Draft to my team", key=f"me_{r.name}",
                                   use_container_width=True):
                    _record_pick(r.name, r.position, mine=True)
                    st.rerun()
                if bcols[1].button("✗ Gone (someone else)", key=f"gone_{r.name}",
                                   use_container_width=True):
                    _record_pick(r.name, r.position, mine=False)
                    st.rerun()

    # ---- reconstructing draft queue: the plan, not just the next pick ----
    st.divider()
    with st.expander("📋 My draft queue — pick order (rebuilds after every pick)", expanded=True):
        st.caption("Plans your next several snake picks against the survival clock: "
                   "take the one who won't last now, queue the one who will for later.")
        try:
            _q = DQ.build_queue(pool, cfg, set(ss.drafted), list(ss.my_roster),
                                int(ss.current_overall), opponents=opps,
                                scoring_key=scoring_key, max_slots=7)
        except Exception as _e:
            _q = []
            st.caption(f"(queue unavailable: {_e})")
        if not _q:
            st.caption("No upcoming picks to plan.")
        for _s in _q:
            _surv = f"{int((_s.survival_here or 0)*100)}% here" if _s.survival_here is not None else ""
            st.markdown(
                f"**R{_s.round_no} · pick {_s.my_overall}** → **{_s.name}** "
                f"({_s.position}·{_s.team})  \n"
                f"<span class='pmeta'>{_s.reason} · {_surv}</span>",
                unsafe_allow_html=True)


def _render_opponent_view(pool, cfg, recs, opps, scoring_key, slot, teams,
                          on_clock_slot, on_clock_name, picks_until_me):
    """Between YOUR picks — read the room. Who's up, what they'll likely take,
    who to snipe, and a slim on-deck peek at your own best-available."""
    my_slot = int(slot)
    # Build per-slot loyalty map: this seat's manager → players they've drafted
    # repeatedly (their 'guys'), so Prophecy predicts the actual player, not just
    # the position. mgr_dna carries favorite_players; slot_to_owner maps seat→mgr.
    import re as _re

    def _ln(s):
        s = (s or "").lower().strip()
        s = _re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
        s = _re.sub(r"[^a-z0-9 ]", "", s)
        return _re.sub(r"\s+", " ", s).strip()

    _loyalty_by_slot = {}
    _mgr_dna_l = ss.get("_mgr_dna") or {}
    _s2o_l = ss.get("_slot_to_owner") or {}
    for _sl, _owner in _s2o_l.items():
        _d = _mgr_dna_l.get(_owner)
        if _d and _d.get("favorite_players"):
            _loyalty_by_slot[int(_sl)] = {_ln(nm): c
                                          for nm, c in _d["favorite_players"].items()}
    # Prophecy rollout over the upcoming picks
    preds = PROPH.predict_board(pool, cfg, set(ss.drafted),
                                int(ss.current_overall), opponents=opps,
                                scoring_key=scoring_key, horizon=int(teams) * 2,
                                loyalty_by_slot=_loyalty_by_slot)

    # 1) the manager on the clock + their most-likely target
    _who = on_clock_name or f"slot {on_clock_slot}"
    now_pred = preds[0] if preds else None
    if now_pred and now_pred.top:
        nm, pos, conf = now_pred.top[0]
        alts = " · ".join(f"{n} ({p})" for n, p, _ in now_pred.top[1:3])
        st.markdown(
            f'<div style="background:linear-gradient(100deg,#170d13,#101014);'
            f'border:1px solid var(--accent2);border-radius:6px;padding:14px 18px;'
            f'margin-bottom:10px;box-shadow:0 0 22px rgba(255,46,136,.18);">'
            f'<span style="font:700 13px \'Space Grotesk\',sans-serif;'
            f'text-transform:uppercase;letter-spacing:.5px;color:var(--accent2);">'
            f'🔮 {_who} likely takes</span><br>'
            f'<span class="pname">{nm}</span> '
            f'<span class="pill pos-{pos}">{pos}</span> '
            f'<span class="pmeta">· {int(conf*100)}% likely</span><br>'
            f'<span class="pmeta">also in play: {alts}</span></div>',
            unsafe_allow_html=True)

    # 2) run detector — is a position getting hammered?
    _run = CO.run_detector([p[2] for p in ss.pick_log])
    if _run:
        st.warning(_run)

    # 3) SNIPE alerts — players an opponent covets that you can grab first
    snipes = PROPH.find_snipes(preds, cfg, min_conf=0.35)
    if snipes:
        st.markdown('<div class="pmeta" style="margin:4px 0 2px;font-weight:700;'
                    'text-transform:uppercase;letter-spacing:.4px;color:var(--accent);">'
                    '⚠ Snipe alerts — grab now to deny</div>',
                    unsafe_allow_html=True)
        for sn in snipes[:4]:
            _sniper = (opps.profiles[sn.coveted_by_slot].name
                       if (opps and sn.coveted_by_slot in opps.profiles
                           and opps.profiles[sn.coveted_by_slot].name)
                       else f"slot {sn.coveted_by_slot}")
            st.markdown(
                f'<div style="background:var(--panel);border:1px solid var(--line);'
                f'border-left:3px solid var(--accent);border-radius:5px;'
                f'padding:8px 13px;margin-bottom:6px;">'
                f'<span class="pname" style="font-size:14px;">{sn.player}</span> '
                f'<span class="pill pos-{sn.position}">{sn.position}</span> '
                f'<span class="pmeta">— {_sniper} wants him at pick '
                f'{sn.their_pick_overall} ({int(sn.confidence*100)}%). '
                f'You pick at {sn.your_pick_overall} — take him first.</span></div>',
                unsafe_allow_html=True)
    else:
        st.caption("No high-confidence snipes right now — the board's calm.")

    # 4) predicted board table up to your next pick
    with st.expander(f"🔮 Predicted picks until your turn (in {picks_until_me})",
                     expanded=False):
        rows = []
        for pr in preds[:picks_until_me + 1]:
            who = ("YOU" if pr.is_me else
                   (opps.profiles[pr.slot].name
                    if (opps and pr.slot in opps.profiles and opps.profiles[pr.slot].name)
                    else f"slot {pr.slot}"))
            tgt = f"{pr.top[0][0]} ({pr.top[0][1]})" if pr.top else "—"
            rows.append({"Pick": pr.overall, "Manager": who,
                         "Likely target": tgt,
                         "Conf": f"{int(pr.top[0][2]*100)}%" if pr.top else ""})
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)

    # 5) slim on-deck peek — your top 3 best-available so you're never blind
    if recs:
        st.markdown('<div class="pmeta" style="margin:8px 0 2px;font-weight:700;'
                    'text-transform:uppercase;letter-spacing:.4px;">'
                    '🎯 On deck for you — top 3 best-available</div>',
                    unsafe_allow_html=True)
        for r in recs[:3]:
            st.markdown(
                f'<span class="pill pos-{r.position}">{r.position}</span> '
                f'<span class="pname" style="font-size:14px;">{r.name}</span> '
                f'<span class="pmeta">{r.team} · VORP {r.vorp} · T{r.tier}</span>',
                unsafe_allow_html=True)
        st.caption("Switch to 🎯 My Pick (or it flips automatically when you're "
                   "on the clock) for the full command center.")


with tab_board:
    _render_board()


# --------------------------------------------------------------------------- stack lab
with tab_stack:
    st.subheader("Stack Lab — schedule softness & correlation")
    st.caption("Art + science: score any QB + WR/TE stack's pass-defense schedule, "
               "weighted toward the fantasy playoff weeks (15-17).")
    qbs = sorted([p.name for p in pool if p.position == "QB"])
    catchers = sorted([p.name for p in pool if p.position in ("WR", "TE")])
    scol = st.columns(2)
    qb_pick = scol[0].selectbox("Quarterback", qbs,
                                index=qbs.index("Matthew Stafford") if "Matthew Stafford" in qbs else 0)
    pc_pick = scol[1].selectbox("Receiver", catchers,
                                index=catchers.index("Davante Adams") if "Davante Adams" in catchers else 0)
    ev = M.evaluate_stack(qb_pick, pc_pick)
    if not ev:
        st.warning("Those two aren't on the same team (or team schedule not loaded). "
                   "Stacks require a shared team.")
    else:
        r = ev.report
        gcol = st.columns(3)
        gcol[0].metric("Stack grade", r.grade.split()[0])
        gcol[1].metric("Soft weeks", r.soft_weeks)
        gcol[2].metric("Playoff softness", f"{r.playoff_softness:+}")
        st.caption(ev.correlation_note)
        rows = []
        for w in r.weeks:
            rows.append({
                "Week": w.week, "Opp": w.opponent or "BYE",
                "PassTD allowed": w.pass_td_allowed if w.pass_td_allowed else "—",
                "Softness": w.softness if w.softness is not None else "—",
                "Playoff": "★" if w.is_playoff_week else "",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)


# --------------------------------------------------------------------------- opponents
with tab_opp:
    st.subheader("Opponent modeling — who will actually be left?")
    st.caption("Set how each manager tends to draft. The board's survival % "
               "('WON'T LAST' / 'CAN WAIT') then reflects the SPECIFIC opponents "
               "picking before your next turn — not a generic ADP curve. "
               "Live picks also nudge profiles automatically as the draft unfolds.")

    with st.expander("😈 Learn each manager's DNA from your league's PAST drafts",
                     expanded=False):
        st.caption("Pulls prior ESPN seasons (read-only, same cookies as "
                   "live-connect) and derives each slot's REAL tendencies — who "
                   "hammers RB early, who's a homer, who fades rookies — then "
                   "auto-fills the profiles below so Prophecy predicts the actual humans.")
        dcol = st.columns([1, 1, 2])
        _dna_default = str(league_id) if str(league_id or "").strip() else ""
        lh_league = dcol[0].text_input("League ID", value=_dna_default, key="lh_league")
        lh_seasons = dcol[1].text_input("Past seasons (comma)", value="2021,2022,2023,2024,2025",
                                        key="lh_seasons")
        st.caption("Cookies are reused from your ESPN connect — to learn a "
                   "different league, just change the League ID above.")
        _s2 = ss.get("espn_s2", "")
        _swid = ss.get("espn_swid", "")
        if not (_s2 and _swid):
            _cc = st.columns(2)
            _s2 = _cc[0].text_input("espn_s2", type="password", key="lh_s2")
            _swid = _cc[1].text_input("SWID", type="password", key="lh_swid")
        if st.button("😈 Pull history & learn DNA (by person)"):
            try:
                _lid = str(lh_league or "").strip()
                if not _lid.isdigit():
                    st.warning("Enter your numeric ESPN League ID first "
                               "(it's blank — connect a league in the sidebar, "
                               "or type the ID here).")
                    st.stop()
                if _s2:
                    ss.espn_s2 = _s2
                if _swid:
                    ss.espn_swid = _swid
                seasons = [int(x) for x in lh_seasons.split(",") if x.strip()]
                if not seasons:
                    st.warning("Enter at least one past season, e.g. 2024,2025.")
                    st.stop()
                drafts = LH.pull_past_drafts(int(_lid), seasons,
                                             ss.espn_s2, ss.espn_swid)
                if not drafts:
                    st.warning("No past drafts found (new league, wrong ID, or "
                               "cookies needed for a private league).")
                else:
                    mgr_dna = LH.learn_dna_by_manager(drafts)
                    slot_owner = LH.current_slot_to_owner(
                        int(_lid), 2026, ss.espn_s2, ss.espn_swid)
                    if slot_owner:
                        applied = LH.apply_manager_dna(opps, mgr_dna, slot_owner)
                        note = (f"Learned {len(mgr_dna)} managers by PERSON; "
                                f"mapped {applied} to this year's seats.")
                    else:
                        # draft order not set yet — fall back to slot-based
                        dna = LH.learn_dna(drafts)
                        LH.apply_dna(opps, dna)
                        note = (f"Learned {len(mgr_dna)} managers by person "
                                f"(this year's draft order not set yet — applied "
                                f"slot-based for now; re-run once the order posts).")
                    ss["_dna"] = {m: d["dossier"] for m, d in mgr_dna.items()}
                    st.success(note)
            except Exception as ex:  # noqa: BLE001
                st.error(f"History pull failed: {ex}")
        if ss.get("_dna"):
            st.markdown("**Manager dossiers:**")
            for s_, doss in sorted(ss["_dna"].items()):
                st.markdown(f"- {doss}")

    tendency_opts = list(O.TENDENCY_POS_BIAS.keys())
    teams_list = sorted({p.team for p in pool})
    for s_ in range(1, int(teams) + 1):
        prof = opps.profiles[s_]
        is_me = (s_ == int(slot))
        with st.container(border=True):
            cc = st.columns([1, 3, 2, 2])
            cc[0].markdown(f"**Slot {s_}**" + (" (YOU)" if is_me else ""))
            if is_me:
                cc[1].caption("your seat — not modeled as an opponent")
                continue
            _safe_default = [t for t in (prof.tendencies or []) if t in tendency_opts]
            prof.tendencies = cc[1].multiselect(
                "Tendencies", tendency_opts, default=_safe_default,
                key=f"tend_{s_}") or ["ADP-robot"]
            prof.rookie_averse = cc[2].checkbox("Rookie-averse", value=prof.rookie_averse,
                                                key=f"rook_{s_}")
            fav = cc[3].selectbox("Homer team", ["(none)"] + teams_list,
                                  index=0 if not prof.favorite_team else
                                  (teams_list.index(prof.favorite_team) + 1),
                                  key=f"fav_{s_}")
            prof.favorite_team = None if fav == "(none)" else fav
    if st.button("Reset all to ADP-robot"):
        for s_, prof in opps.profiles.items():
            prof.tendencies = ["ADP-robot"]
            prof.rookie_averse = False
            prof.favorite_team = None
        st.rerun()


# --------------------------------------------------------------------------- strategy sim
with tab_sim:
    st.subheader("Optimal build — the best team you can draft from your slot")
    st.caption("Your opponents draft realistically off each seat's learned DNA "
               "+ loyalty picks + ADP (the crystal ball), so 'who's left at your "
               "pick' mirrors your real league. At every one of YOUR picks the sim "
               "takes the player that adds the most to your projected starting "
               "lineup — the highest-scoring team you can actually assemble, not a "
               "rigid strategy template.")
    if st.button("Run simulation"):
        try:
            # crystal-ball opponents: build the per-slot loyalty map (same as
            # Prophecy) so bots draft their DNA tendencies + repeat 'guys'
            import re as _re

            def _lnn(s):
                s = (s or "").lower().strip()
                s = _re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
                s = _re.sub(r"[^a-z0-9 ]", "", s)
                return _re.sub(r"\s+", " ", s).strip()

            _loy = {}
            _mdna = ss.get("_mgr_dna") or {}
            _s2o = ss.get("_slot_to_owner") or {}
            for _sl, _own in _s2o.items():
                _dd = _mdna.get(_own)
                if _dd and _dd.get("favorite_players"):
                    _loy[int(_sl)] = {_lnn(nm): c
                                      for nm, c in _dd["favorite_players"].items()}
            results = SIM.compare_strategies(pool, cfg, scoring_key,
                                             opponents=opps, loyalty_by_slot=_loy)
            for res in results:
                with st.container(border=True):
                    st.markdown(f"**⭐ OPTIMAL BUILD** — "
                                f"proj starting pts: {res.get('total_points','?')}")
                    if res.get("summary"):
                        st.caption(res["summary"])
                    # map player name -> "R{round} · P{overall}" from the pick log
                    _tm = int(teams)
                    _when = {}
                    for _ov, _nm, _ps in res.get("picks", []):
                        _rd = (_ov - 1) // _tm + 1
                        _when[_nm] = f"R{_rd} · P{_ov}"
                    if res.get("lineup"):
                        st.write({slot: (f"{v[0]} ({v[1]}) {v[2]}pts"
                                         f"  —  {_when.get(v[0], '')}")
                                  for slot, v in res["lineup"].items()})
                # --- all 12 projected teams (opponents drafted by their DNA) ---
                teams = res.get("all_teams") or []
                if teams:
                    ranked = sorted(teams, key=lambda t: -t["total_points"])
                    with st.expander(f"📋 All {len(teams)} projected rosters "
                                     f"(opponents drafted by their owner DNA)"):
                        for ti, t in enumerate(ranked, 1):
                            tag = " 👈 YOU" if t["is_me"] else ""
                            st.markdown(f"**{ti}. {t['owner']}{tag}** — "
                                        f"{t['total_points']:.0f} proj starting pts")
                            if t["is_me"]:
                                st.write({slot: (f"{v[0]} ({v[1]})  —  "
                                                 f"{_when.get(v[0], '')}")
                                          for slot, v in t["lineup"].items()
                                          if v[0]})
                            else:
                                st.write({slot: f"{v[0]} ({v[1]})"
                                          for slot, v in t["lineup"].items()
                                          if v[0]})
        except Exception as ex:  # noqa: BLE001
            st.error(f"Sim error: {ex}")


# --------------------------------------------------------------------------- weekly lineup
with tab_week:
    st.subheader("Weekly lineup optimizer — start/sit + why")
    st.caption("Pick the optimal starters for a given week and get a plain-English "
               "reason for every start/sit (matchup, dome, role, injury).")
    if not ss.my_roster:
        st.info("Draft some players first (Board or Enter Picks tab).")
    else:
        wk = st.number_input("Week", 1, 18, 15)
        decisions = LO.optimize_week(list(ss.my_roster), pool, cfg, int(wk), scoring_key)
        st.markdown("### Starters")
        for d in decisions:
            if d.started:
                st.success(f"**{d.name}** ({d.slot}) — {d.weekly_points} pts  \n{d.narrative}")
        st.markdown("### Bench")
        for d in decisions:
            if not d.started:
                st.warning(f"**{d.name}** ({d.position}) — {d.weekly_points} pts  \n{d.narrative}")

        # matchup-tilt close calls (season_tools cross-check)
        _ssq = SEA.start_sit(list(ss.my_roster), pool, cfg, int(wk), scoring_key)
        if _ssq.close_calls:
            st.markdown("### ⚖️ Close calls (matchup-adjusted)")
            for cc in _ssq.close_calls:
                st.info(cc)

    st.divider()
    st.markdown("### 🔀 Trade evaluator")
    st.caption("Enter players by name (comma-separated) for each side. Scored on "
               "season VORP + best-starter value, with a plain verdict.")
    _names = sorted(n for n, _ in ss.my_roster) if ss.my_roster else []
    _allnames = [p.name for p in pool]
    _give = st.multiselect("You GIVE", options=_allnames, default=[],
                           key="trade_give")
    _get = st.multiselect("You GET", options=_allnames, default=[],
                          key="trade_get")
    if _give and _get:
        _pm = {p.name: p.position for p in pool}
        tv = SEA.evaluate_trade([(n, _pm.get(n, "")) for n in _give],
                                [(n, _pm.get(n, "")) for n in _get],
                                pool, cfg, scoring_key)
        _vc = {"WIN": st.success, "FAIR": st.info, "LOSE": st.error}[tv.verdict]
        _vc(f"**{tv.verdict}** — {tv.note}")
        _c1, _c2 = st.columns(2)
        _c1.markdown("**Give**  \n" + "  \n".join(f"{n}: {v} VORP" for n, v in tv.give.detail))
        _c2.markdown("**Get**  \n" + "  \n".join(f"{n}: {v} VORP" for n, v in tv.get.detail))


# --------------------------------------------------------------------------- prophecy
with tab_proph:
    st.subheader("🔮 Draft Prophecy — what each rival takes next")
    st.caption("Predicts every upcoming pick from each manager's tendencies + ADP + "
               "value, then flags SNIPES: players a rival covets that you can grab "
               "first. Set opponent styles in the Opponents tab to sharpen it.")
    horizon = st.slider("Look ahead (picks)", 6, 36, 24)
    preds = PROPH.predict_board(pool, cfg, set(ss.drafted),
                                int(ss.current_overall), opponents=opps,
                                scoring_key=scoring_key, horizon=int(horizon))
    snipes = PROPH.find_snipes(preds, cfg)
    if snipes:
        st.markdown("#### 🎯 Snipe targets — take them first to deny a rival")
        for s in snipes[:6]:
            st.markdown(
                f'<div style="background:linear-gradient(100deg,#2a1020,#3a1420);'
                f'border:1px solid #f87171;border-radius:12px;padding:10px 14px;'
                f'margin-bottom:6px;">'
                f'<b style="color:#e8eef7;">{s.player}</b> '
                f'<span class="pill pos-{s.position}">{s.position}</span> '
                f'<span class="pmeta">— slot {s.coveted_by_slot} wants him at pick '
                f'{s.their_pick_overall} ({int(s.confidence*100)}% likely). '
                f'Grab him at your pick {s.your_pick_overall} to snipe.</span></div>',
                unsafe_allow_html=True)
    st.markdown("#### Predicted board")
    rows = []
    for p in preds:
        who = "🫵 YOU" if p.is_me else f"slot {p.slot}"
        picks_str = " · ".join(f"{nm} ({pos}) {int(c*100)}%" for nm, pos, c in p.top)
        rows.append({"Overall": p.overall, "Team": who, "Likely picks": picks_str})
    st.dataframe(rows, use_container_width=True, hide_index=True)


# --------------------------------------------------------------------------- roster
with tab_roster:
    st.subheader("Your roster")
    if not ss.my_roster:
        st.info("No players yet. Draft from the Board tab.")
    else:
        for n, pos in ss.my_roster:
            raw = name_to_raw.get(n)
            bye = f" · bye {raw.bye}" if raw and raw.bye else ""
            st.write(f"- **{n}** ({pos}{bye})")
        needs = X.open_needs(X.Roster(players=list(ss.my_roster)), cfg)
        open_slots = {p: v for p, v in needs.items() if v > 0}
        st.markdown("**Open starting needs:** " +
                    (", ".join(f"{p}×{v:g}" for p, v in open_slots.items()) or "all filled"))

        # ---- championship title-fit scorecard ----
        st.markdown("### 🏆 Championship title-fit")
        sc = A.score_roster(list(ss.my_roster), pool, cfg, scoring_key)
        st.progress(min(1.0, sc.total / 100.0), text=f"{sc.total}/100")
        ccols = st.columns(len(sc.components))
        for (k, v), col in zip(sc.components.items(), ccols):
            col.metric(k, v)
        if sc.strengths:
            st.success("**Strengths:** " + " · ".join(sc.strengths))
        if sc.flags:
            st.warning("**Gaps to fix:**\n\n" + "\n".join(f"- {f}" for f in sc.flags))

    # ---- trash talk (paste in league chat) ----
    if ss.get("_last_trash"):
        tcol = st.columns([4, 1])
        tcol[0].markdown(f'<div style="background:#1a2334;border:1px solid #233046;'
                         f'border-radius:10px;padding:10px 14px;color:#fcd34d;'
                         f'font-weight:600;">🗣️ {ss["_last_trash"]}</div>',
                         unsafe_allow_html=True)
        if tcol[1].button("🔁 New line") and ss.my_roster:
            ss["_last_trash"] = CO.trash_talk(ss.my_roster[-1][0])
            st.rerun()

    # ---- regret journal ----
    if ss.regret:
        with st.expander(f"📓 Regret journal ({len(ss.regret)} passed players)"):
            for nm, ov, by in reversed(ss.regret[-15:]):
                st.markdown(f"- Passed **{nm}** — went at pick {ov} to {by}")

    if st.button("Reset draft"):
        ss.drafted = set()
        ss.my_roster = []
        ss.current_overall = 1
        ss.pick_log = []
        ss.team_rosters = {}
        ss.undo_stack = []
        ss.regret = []
        ss["_last_trash"] = ""
        ss["_sim"] = None
        st.rerun()


# --------------------------------------------------------------------------- waiver wire
with tab_waiver:
    st.subheader("📡 Waiver Wire — the in-season add/drop engine")
    st.caption("After the draft: who to grab off the wire and who to cut. Ranks "
               "free agents by ROS value + opportunity + matchup + injury openings "
               "+ breakout lean. Connect a league to auto-detect who's actually free, "
               "or paste a taken list.")

    # figure out who's rostered in the league (free agents = pool − rostered)
    rostered: set = set()
    _fa_source = "manual"
    wc = st.columns([1.4, 1, 1])
    if ss.get("espn"):
        if wc[0].button("🔄 Pull rosters from ESPN (find free agents)"):
            try:
                with st.spinner("Reading league rosters…"):
                    rp = ss.espn.rostered_players()
                ss["_rostered"] = list(rp["names"])
                ss["_rostered_by_team"] = rp["by_team"]
                st.success(f"{len(rp['names'])} players rostered across the league "
                           "— everyone else is a free agent.")
            except Exception as ex:  # noqa: BLE001
                st.error(f"Couldn't read rosters: {ex}")
    if ss.get("_rostered"):
        rostered = set(ss["_rostered"])
        _fa_source = "espn"
        st.caption(f"✅ Free agents computed from your live ESPN league "
                   f"({len(rostered)} rostered).")
    else:
        # fallback: treat drafted + my_roster as taken, plus a manual paste
        rostered = set(ss.drafted) | {n for n, _ in ss.my_roster}
        _paste = st.text_area("Or paste taken players (one per line) to exclude",
                              height=80, key="wv_taken")
        if _paste.strip():
            rostered |= {ln.strip() for ln in _paste.splitlines() if ln.strip()}
        st.caption("Using drafted/rostered players as 'taken'. Connect ESPN + pull "
                   "rosters for exact free agents in your league.")

    pos_filter = wc[1].multiselect("Positions", ["QB", "RB", "WR", "TE", "K", "DST"],
                                   key="wv_pos")
    faab_budget = wc[2].number_input("FAAB budget ($)", 0, 1000, 100, key="wv_faab")

    targets = WV.find_waiver_targets(pool, cfg, rostered, scoring_key,
                                     positions=pos_filter or None, top_n=30,
                                     my_roster=list(ss.my_roster))
    if not targets:
        st.info("No available free agents match — widen the position filter or "
                "reduce the taken list.")
    else:
        _flt = st.radio("Show", ["All", "⭐ Stars only", "🚀 Rockets only",
                                 "⭐/🚀 Flagged only"], horizontal=True,
                        label_visibility="collapsed", key="wv_flag_filter")
        _shown = targets
        if _flt == "⭐ Stars only":
            _shown = [t for t in targets if t.star]
        elif _flt == "🚀 Rockets only":
            _shown = [t for t in targets if t.rocket]
        elif _flt == "⭐/🚀 Flagged only":
            _shown = [t for t in targets if t.star or t.rocket]
        _n_star = sum(1 for t in targets if t.star)
        _n_rocket = sum(1 for t in targets if t.rocket)
        st.markdown(f"### 🎯 Top pickups &nbsp;"
                    f'<span class="pmeta">⭐ {_n_star} stars · 🚀 {_n_rocket} rockets</span>',
                    unsafe_allow_html=True)
        if not _shown:
            st.caption("None flagged in the current list — switch back to All.")
        for i, t in enumerate(_shown[:20]):
            _pcolor = {"MUST-ADD": "var(--accent)", "STRONG": "var(--good)",
                       "SPECULATIVE": "var(--warn)", "STASH": "var(--dim)"}.get(
                           t.priority, "var(--dim)")
            with st.container(border=True):
                cols = st.columns([3.2, 0.9, 0.9, 1.1, 4])
                star = " ⭐" if i == 0 else ""
                _tags = ""
                if t.rocket:
                    _tags += ('<span class="bdg" style="border-color:var(--warn);'
                              'color:var(--warn);">🚀 ROCKET</span> ')
                if t.star:
                    _tags += ('<span class="bdg" style="border-color:var(--accent);'
                              'color:var(--accent);">⭐ STAR</span> ')
                _iconcolor = {"🔥": "var(--bad)", "🕳️": "var(--accent2)",
                              "📅": "var(--good)", "🩹": "var(--warn)",
                              "🪤": "var(--dim)", "💎": "var(--soft, var(--accent))"}
                for label, tip in (t.icons or []):
                    _c = next((v for k, v in _iconcolor.items() if label.startswith(k)),
                              "var(--dim)")
                    _tags += (f'<span class="bdg" title="{tip}" '
                              f'style="border-color:{_c};color:{_c};">{label}</span> ')
                cols[0].markdown(
                    f'<span class="pill pos-{t.position}">{t.position}</span> '
                    f'<span class="pname">{t.name}{star}</span> {_tags}<br>'
                    f'<span class="pmeta">{t.team} · {t.ros_points:.0f} pts ROS'
                    + (f' · 🏥 {t.injury_note}' if t.injury_note else '') + '</span>'
                    + (f'<br><span class="pmeta" style="color:var(--accent);">⭐ '
                       f'{t.star_note}</span>' if t.star and t.star_note else ''),
                    unsafe_allow_html=True)
                cols[1].metric("Score", t.pickup_score)
                cols[2].metric("ROS VORP", t.ros_vorp)
                # FAAB in real dollars off the budget
                _bid = max(1, round(t.faab_pct / 100 * faab_budget)) if faab_budget else t.faab_pct
                cols[3].metric("FAAB bid", f"${_bid}" if faab_budget else f"{t.faab_pct}%")
                cols[4].markdown(
                    f'<span class="bdg" style="border-color:{_pcolor};color:{_pcolor};">'
                    f'{t.priority}</span> '
                    + " ".join(f'<span class="bdg bdg-soft">{r}</span>'
                               for r in t.reasons),
                    unsafe_allow_html=True)

    # drop candidates from MY roster
    if ss.my_roster:
        st.markdown("### 🪓 Drop candidates (make room)")
        drops = WV.drop_candidates(list(ss.my_roster), pool, cfg, scoring_key, n=5)
        for d in drops:
            st.markdown(
                f'<span class="pill pos-{d.position}">{d.position}</span> '
                f'<span class="pname" style="font-size:14px;">{d.name}</span> '
                f'<span class="pmeta">— {d.reason}</span>',
                unsafe_allow_html=True)
    else:
        st.caption("Draft/roster some players and they'll show here as drop options.")


# --------------------------------------------------------------------------- rankings
with tab_rank:
    st.subheader("📊 Rankings — consensus ADP vs Shredder's own board")
    st.caption("Consensus ADP blends every reliable public source we can fetch "
               "(FantasyPros expert consensus + FantasyFootballCalculator live "
               "mock ADP + Sleeper), averaged per player. Shredder Rank is OUR "
               "answer — every player scored by the full Edge Engine: projection → "
               "VORP → tiers + opportunity (target/snap/O-line/pace) + matchup "
               "softness + consistency + injury + breakout lean. Delta shows where "
               "we disagree with the market.")
    rc = st.columns([1, 1, 1])
    _rpos = rc[0].multiselect("Position", ["QB", "RB", "WR", "TE", "K", "DST"],
                              key="rk_pos")
    _rverdict = rc[1].multiselect("Verdict", ["VALUE", "FAIR", "REACH"], key="rk_verdict")
    _rtopn = rc[2].number_input("Show top N", 25, 300, 150, step=25, key="rk_topn")

    rankings = SR.build_rankings(pool, cfg, scoring_key, top_n=int(_rtopn))
    shown = [r for r in rankings
             if (not _rpos or r.position in _rpos)
             and (not _rverdict or r.verdict in _rverdict)]

    _n_val = sum(1 for r in rankings if r.verdict == "VALUE")
    _n_reach = sum(1 for r in rankings if r.verdict == "REACH")
    st.markdown(f'<span class="pmeta">🟢 {_n_val} values · 🔴 {_n_reach} reaches · '
                f'{len(rankings)} ranked · consensus from up to 3 sources</span>',
                unsafe_allow_html=True)

    _table = []
    for r in shown:
        _vcolor = {"VALUE": "🟢", "REACH": "🔴", "FAIR": "⚪"}.get(r.verdict, "")
        _table.append({
            "Shredder": r.shredder_rank,
            "Player": r.name,
            "Pos": f"{r.position}{r.pos_rank}",
            "Tier": f"T{r.tier}",
            "Team": r.team,
            "Consensus ADP": r.consensus_adp if r.consensus_adp else "—",
            "Δ": (f"{'+' if (r.delta or 0) >= 0 else ''}{r.delta}"
                  if r.delta is not None else "—"),
            "Read": f"{_vcolor} {r.verdict}",
            "Srcs": r.adp_sources,
            "Composite": r.composite,
            "VORP": r.vorp,
            "Why": " · ".join(r.badges),
        })
    st.dataframe(_table, use_container_width=True, hide_index=True, height=560)

    # CSV download (emoji stripped for Excel)
    import io as _io, csv as _csv, re as _re
    _EMO = _re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")
    _buf = _io.StringIO()
    if _table:
        w = _csv.DictWriter(_buf, fieldnames=list(_table[0].keys()))
        w.writeheader()
        for row in _table:
            w.writerow({k: _EMO.sub("", str(v)).strip() for k, v in row.items()})
    st.download_button("⬇️ Download rankings (CSV)", _buf.getvalue().encode("utf-8-sig"),
                       file_name="shredder_rankings_2026.csv", mime="text/csv")


# --------------------------------------------------------------------------- shadow ledger
with tab_ledger:
    st.subheader("📒 Shadow Ledger — how Shredder's picks would have done")
    st.caption("At every pick we logged what Shredder recommended vs what you "
               "actually drafted. Each week (auto-scored Tuesdays) we tally both "
               "rosters' real fantasy points, so you see whether the copilot's "
               "calls beat yours — cumulatively, all season.")
    _led = SLG.load()
    _picks = _led.get("picks", [])
    if not _picks:
        st.info("No picks logged yet. Draft in the app (Board or Enter Picks) and "
                "each of your picks records Shredder's shadow recommendation "
                "alongside it. Weekly scoring starts once the season begins.")
    else:
        _cum = SLG.cumulative(_led)
        mc = st.columns(3)
        mc[0].metric("Your team (season)", _cum["actual_total"])
        mc[1].metric("🕶️ Shadow team (season)", _cum["shadow_total"])
        mc[2].metric("Shadow − You", f"{'+' if _cum['delta_total']>=0 else ''}"
                     f"{_cum['delta_total']}",
                     help="Positive = Shredder's team would have outscored yours")

        if _cum["series"]:
            import pandas as _pd
            _df = _pd.DataFrame(_cum["series"])
            st.line_chart(_df.set_index("week")[["actual_cum", "shadow_cum"]],
                          color=["#ececef", "#e8ff53"])
            st.caption(f"Cumulative points by week · {_cum['weeks_scored']} week(s) scored.")

        st.markdown("### Pick-by-pick verdict")
        _verd = SLG.per_player_verdict(_led)
        _vtable = []
        for v in _verd:
            _vi = {"SHREDDER WON": "🕶️", "YOU WON": "✅", "SAME PICK": "🤝",
                   "TIE": "➖"}.get(v["verdict"], "")
            _vtable.append({
                "Rd": v["round"], "Overall": v["overall"],
                "You drafted": v["actual"], "Your pts": v["actual_pts"],
                "Shredder wanted": v["shredder"], "Shadow pts": v["shredder_pts"],
                "Verdict": f"{_vi} {v['verdict']}",
            })
        st.dataframe(_vtable, use_container_width=True, hide_index=True)

        with st.expander("🕶️ Full shadow roster (what Shredder would have built)"):
            for nm, pos in SLG.shadow_roster(_led):
                st.markdown(f'<span class="pill pos-{pos}">{pos}</span> '
                            f'<span class="pname" style="font-size:14px;">{nm}</span>',
                            unsafe_allow_html=True)
        st.caption("Auto-scored every Tuesday by the `shadow_score` cron. "
                   "To force a score now, trigger that cron or run it with a week number.")


# --------------------------------------------------------------------------- live & lines
with tab_live:
    st.subheader("🎲 Live & Lines — scores, betting lines, upset radar")
    st.caption("Live NFL scoreboard with betting lines (spread / over-under) from "
               "ESPN's free feed. The Upset Radar flags a pregame favorite in "
               "trouble — heat rises with a bigger favorite trailing later in the "
               "game. Not betting advice; just a live read on where chalk is cracking.")
    if st.button("🔄 Refresh live games"):
        ss["_live_games"] = LG.fetch_live_games()
    if "_live_games" not in ss:
        ss["_live_games"] = LG.fetch_live_games()
    _games = ss.get("_live_games") or []

    if not _games:
        st.info("No games available from the feed right now (offline, or no slate "
                "today). Hit refresh during game windows.")
    else:
        _alerts = [g for g in _games if g.status == "in" and g.upset_heat >= 40]
        if _alerts:
            for g in _alerts:
                st.markdown(
                    f'<div style="background:linear-gradient(100deg,#170d13,#1a0f0a);'
                    f'border:1px solid var(--bad);border-left:3px solid var(--bad);'
                    f'border-radius:6px;padding:10px 15px;margin-bottom:8px;'
                    f'box-shadow:0 0 20px rgba(255,77,94,.2);">'
                    f'<span style="font:700 13px \'Space Grotesk\',sans-serif;'
                    f'color:var(--bad);text-transform:uppercase;">🚨 UPSET BREWING</span> '
                    f'<span class="pmeta">heat {int(g.upset_heat)}/100</span><br>'
                    f'<span style="color:#f5b6b6;">{g.upset_note}</span> — '
                    f'{g.away} {g.away_score} @ {g.home} {g.home_score}</div>',
                    unsafe_allow_html=True)

        _live = [g for g in _games if g.status == "in"]
        _other = [g for g in _games if g.status != "in"]

        # model-vs-market VALUE EDGES (our live win-prob diverges from the book)
        _edges = sorted([g for g in _live if abs(g.edge_fav) >= 0.08],
                        key=lambda g: abs(g.edge_fav), reverse=True)
        for g in _edges:
            _side = g.favorite if g.edge_fav > 0 else (
                g.away if g.favorite == g.home else g.home)
            _mp = int((g.model_p_fav or 0) * 100)
            _kp = int((g.market_p_fav or 0) * 100)
            st.markdown(
                f'<div style="background:linear-gradient(100deg,#0d1a10,#101410);'
                f'border:1px solid var(--accent);border-left:3px solid var(--accent);'
                f'border-radius:6px;padding:10px 15px;margin-bottom:8px;">'
                f'<span style="font:700 13px \'Space Grotesk\',sans-serif;'
                f'color:var(--accent);text-transform:uppercase;">💹 VALUE EDGE — '
                f'{g.edge_note}</span> '
                f'<span class="pmeta">edge {abs(g.edge_fav)*100:.0f} pts</span><br>'
                f'<span style="color:#d7f5c9;">{g.away} @ {g.home} — my model '
                f'{_mp}% fav vs book {_kp}% → lean <b>{_side}</b></span></div>',
                unsafe_allow_html=True)

        for label, group in (("● LIVE NOW", _live), ("Scheduled / Final", _other)):
            if not group:
                continue
            st.markdown(f'<div class="pmeta" style="margin:8px 0 4px;font-weight:700;'
                        f'text-transform:uppercase;letter-spacing:.4px;">{label}</div>',
                        unsafe_allow_html=True)
            for g in group:
                _heatbar = ""
                if g.status == "in" and g.upset_heat > 0:
                    _c = "var(--bad)" if g.upset_heat >= 60 else ("var(--warn)"
                          if g.upset_heat >= 40 else "var(--dim)")
                    _heatbar = (f'<div style="height:5px;background:var(--line);'
                                f'border-radius:3px;margin-top:6px;">'
                                f'<div style="height:5px;width:{int(g.upset_heat)}%;'
                                f'background:{_c};border-radius:3px;"></div></div>')
                _line = (f'{g.spread}' + (f' · O/U {g.over_under}' if g.over_under else '')) \
                    if g.spread else 'no line'
                _wp = ""
                if g.status == "in" and g.model_p_fav is not None:
                    _e = g.edge_fav
                    _ecol = ("var(--accent)" if abs(_e) >= 0.08 else "var(--dim)")
                    _wp = (f'<br><span class="pmeta">🧮 my model '
                           f'{int(g.model_p_fav*100)}% {g.favorite} · book '
                           f'{int((g.market_p_fav or 0)*100)}% · '
                           f'<span style="color:{_ecol};">edge '
                           f'{"+" if _e>=0 else ""}{_e*100:.0f}'
                           f'{(" — "+g.edge_note) if g.edge_note else ""}</span></span>')
                with st.container(border=True):
                    st.markdown(
                        f'<span class="pname">{g.away} {g.away_score} @ '
                        f'{g.home} {g.home_score}</span> '
                        f'<span class="pmeta">· {g.detail}</span><br>'
                        f'<span class="bdg bdg-soft">{_line}</span>'
                        + (f' <span class="pmeta">· {g.upset_note}</span>'
                           if g.upset_note else '')
                        + _wp + _heatbar,
                        unsafe_allow_html=True)


# --------------------------------------------------------------------------- value history
with tab_value:
    st.subheader("📈 Value History — who beats their draft slot (2021–2025)")
    st.caption("5 seasons of real data: where players were DRAFTED at their "
               "position vs where they FINISHED. Within-position, so it's real "
               "skill/role signal — not the 'QBs go late' artifact. Actuals "
               "computed from nflverse play-by-play; ADP from historical mock drafts.")
    import os as _os2, json as _json2
    _vpath = _os2.path.join(_os2.path.dirname(__file__), "data", "value_study_5yr.json")
    if not _os2.path.exists(_vpath):
        st.info("Study data not found — run the value-study builder to generate "
                "data/value_study_5yr.json.")
    else:
        _vs = _json2.load(open(_vpath, encoding="utf-8"))
        st.markdown(
            '<div style="background:var(--panel);border:1px solid var(--line);'
            'border-left:3px solid var(--accent);border-radius:6px;padding:10px 14px;'
            'margin-bottom:10px;"><span style="color:var(--accent);font-weight:700;">'
            'What 5 years say</span><br><span class="pmeta">'
            '📈 Overachievers = RBs who inherit a bellcow role (injury / depth-chart '
            'opening) — opportunity beats talent. 📉 Busts = injury or absence, '
            'overwhelmingly. Both are already levers in the board (role/opportunity '
            '+ injury), shown here as evidence, not a value bump.</span></div>',
            unsafe_allow_html=True)

        cA, cB = st.columns(2)
        with cA:
            st.markdown("### 📈 Top overachievers")
            for r in _vs.get("top_overachievers", [])[:12]:
                st.markdown(
                    f'<span class="pill pos-{r["pos"]}">{r["pos"]}</span> '
                    f'<span class="pname" style="font-size:14px;">{r["name"]}</span> '
                    f'<span class="pmeta">{r["year"]} · {r["pos"]}{r["pos_adp_rank"]} '
                    f'drafted → {r["pos"]}{r["pos_finish_rank"]} finish '
                    f'<b style="color:var(--good);">(+{r["gap"]})</b></span>',
                    unsafe_allow_html=True)
        with cB:
            st.markdown("### 📉 Biggest busts")
            for r in _vs.get("top_busts", [])[:12]:
                st.markdown(
                    f'<span class="pill pos-{r["pos"]}">{r["pos"]}</span> '
                    f'<span class="pname" style="font-size:14px;">{r["name"]}</span> '
                    f'<span class="pmeta">{r["year"]} · {r["pos"]}{r["pos_adp_rank"]} '
                    f'drafted → {r["pos"]}{r["pos_finish_rank"]} finish '
                    f'<b style="color:var(--bad);">({r["gap"]})</b></span>',
                    unsafe_allow_html=True)

        st.markdown("### Browse a season")
        _yr = st.selectbox("Season", ["2025", "2024", "2023", "2022", "2021"],
                           key="vh_year")
        _byyr = _vs.get("by_year", {}).get(_yr, [])
        _pf = st.multiselect("Position", ["QB", "RB", "WR", "TE"], key="vh_pos")
        _scored = [r for r in _byyr if r.get("gap") is not None
                   and (not _pf or r["pos"] in _pf)]
        _scored.sort(key=lambda r: r["gap"], reverse=True)
        _tbl = [{"Player": r["name"], "Pos": r["pos"],
                 "Drafted": f'{r["pos"]}{r["pos_adp_rank"]}',
                 "Finished": f'{r["pos"]}{r["pos_finish_rank"]}',
                 "Gap": f'{"+" if r["gap"]>=0 else ""}{r["gap"]}',
                 "Pts": r["pts"]} for r in _scored]
        st.dataframe(_tbl, use_container_width=True, hide_index=True, height=420)
        st.caption("Gap = positional draft rank − positional finish rank. "
                   "Positive = beat their draft slot.")


# --------------------------------------------------------------------------- roster lab
with tab_lab:
    st.subheader("🧬 Roster Lab — the structural edges")
    st.caption("Bye collisions, same-team clusters, the tier cliff on positions you "
               "still need, handcuffs, and your fantasy-playoff (wks 15-17) slate. "
               "All informational — it never changes a player's value.")
    _mr = list(ss.my_roster)
    _dr = set(ss.drafted)

    # Tier-cliff alarm — most actionable, put it first
    st.markdown("#### ⛰️ Tier-cliff / roster-need alarm")
    _cliffs = RLAB.tier_cliff(pool, _dr, _mr, scoring_key)
    _urgent = [c for c in _cliffs if c.urgency in ("now", "soon")]
    if not _cliffs:
        st.success("No positional needs flagged — roster targets met.")
    else:
        for c in _cliffs:
            _ic = {"now": "🚨", "soon": "⚠️", "ok": "✅"}[c.urgency]
            (st.error if c.urgency == "now" else st.warning if c.urgency == "soon"
             else st.info)(f"{_ic} **{c.position}** — {c.note}")

    st.markdown("#### 🏆 Fantasy-playoff slate (weeks 15-17)")
    if not _mr:
        st.caption("Draft players to grade their playoff schedules.")
    else:
        for name, pos in _mr:
            if pos in ("K", "DST"):
                continue
            _ps = RLAB.playoff_slate(name, pool)
            if _ps:
                _wk = " · ".join(f"wk{w} vs {o or 'BYE'}" for w, o in _ps.weeks)
                _col = {"A": "🟢", "B": "🟢", "C": "🟡", "D": "🔴"}[_ps.grade]
                st.markdown(f"{_col} **{name}** ({_ps.team}) — grade {_ps.grade}: {_ps.note}  \n"
                            f"<span class='pmeta'>{_wk}</span>", unsafe_allow_html=True)

    st.markdown("#### 🔒 Handcuffs (contingent value)")
    _hcs = RLAB.handcuffs(pool, _dr, _mr, scoring_key)
    if not _hcs:
        st.caption("Draft an RB to see his handcuff.")
    for h in _hcs:
        (st.info if h.backup_available else st.caption)(
            ("🟢 " if h.backup_available else "⚫ ") + h.note)

    st.markdown("#### 📅 Bye-week collisions")
    _byes = RLAB.bye_collisions(_mr, pool)
    _bad = [b for b in _byes if b.severity in ("danger", "warn")]
    if not _mr:
        st.caption("Draft players to check bye overlap.")
    elif not _bad:
        st.success("No dangerous bye stacking on your roster.")
    else:
        for b in _bad:
            _names = ", ".join(f"{n} ({p})" for n, p in b.players)
            (st.error if b.severity == "danger" else st.warning)(
                f"**Week {b.week}** — {b.note}  \n<span class='pmeta'>{_names}</span>")

    st.markdown("#### 🔗 Same-team clusters")
    _cl = RLAB.stack_clusters(_mr, pool)
    if not _cl:
        st.caption("No multi-player team clusters yet.")
    for c in _cl:
        (st.success if c.kind == "stack" else st.warning)(
            ("🔗 " if c.kind == "stack" else "⚠️ ") + c.note
            + " — " + ", ".join(n for n, _ in c.players))


# --------------------------------------------------------------------------- manual picks
with tab_picks:
    st.subheader("Enter picks (manual mode)")
    st.caption("Tap players off as they're drafted anywhere in the room. "
               "Use this for ESPN/Yahoo/NFL or any offline draft.")
    avail = sorted([p.name for p in pool if p.name not in ss.drafted])
    who = st.selectbox("Player drafted", avail) if avail else None
    mcol = st.columns(3)
    if who and mcol[0].button("Drafted by ME"):
        raw = name_to_raw[who]
        _record_pick(who, raw.position, mine=True)
        st.rerun()
    if who and mcol[1].button("Drafted by SOMEONE ELSE"):
        _record_pick(who, name_to_raw[who].position, mine=False)
        st.rerun()
    mcol[2].metric("Drafted so far", len(ss.drafted))
