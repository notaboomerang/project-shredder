"""
Fantasy Draft Assistant — a focused, single-screen live-draft copilot.

Run:  streamlit run app.py

The whole app is built around ONE loop:
    1. See the single best pick for YOUR roster right now (the hero card).
    2. Draft it  -> it joins your team, the board requeues instantly.
    3. Someone else takes a player -> mark them gone (one search box, one click),
       and the next-best pick for your roster pops up automatically.

Three ways to run a draft, all sharing that loop:
    • Mock      — practice against AI bots that pick between your turns.
    • Manual    — you drive every pick by hand (works for any platform).
    • ESPN      — connect a league and sync live picks from ESPN's API.

The heavy lifting (VORP, tiers, survival odds, opponent-aware need) lives in the
engine modules; this file is just a clean surface over edge_engine.recommend().
The sprawling previous UI is preserved as app_legacy.py.
"""
from __future__ import annotations

import copy
import re

import streamlit as st

import engine as E
import projections as P
import edge_engine as X
import opponents as O
import mock_draft as MOCK
import shredder_rankings as SR
import lineup_optimizer as LO
import draft_insights as DI
import copilot as CO
import prophecy as PROPH
try:
    import league_history as LH
except Exception:  # noqa: BLE001
    LH = None

# Optional integrations — the app runs fully without any of them.
try:
    import espn_client as EC
except Exception:  # noqa: BLE001
    EC = None
try:
    import secrets_store as SEC
except Exception:  # noqa: BLE001
    SEC = None
try:
    import espn_login as ELOGIN
except Exception:  # noqa: BLE001
    ELOGIN = None
try:
    import saved_leagues as SL
except Exception:  # noqa: BLE001
    SL = None
try:
    import live_feed as _LF
    _norm = _LF._norm
except Exception:  # noqa: BLE001
    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# --------------------------------------------------------------------------- page
import os as _os
import base64 as _b64

_ASSETS = _os.path.join(_os.path.dirname(__file__), "assets")


def _detect_cloud() -> bool:
    """True when running on a shared/hosted server (Streamlit Community Cloud
    etc.), False on your local desktop. On the cloud we must isolate every user
    to their own browser session (no shared cookie/league files); on desktop we
    keep the existing single-user file behavior. Signals, any of which = cloud:
      • an explicit IS_CLOUD=1 env var or secret (you can force it),
      • the app is running from Streamlit Cloud's mount path (/mount/src/...),
      • common hosted-env markers.
    Kept deliberately conservative so it NEVER misfires on the desktop."""
    try:
        if str(_os.environ.get("IS_CLOUD", "")).strip() in ("1", "true", "yes"):
            return True
    except Exception:  # noqa: BLE001
        pass
    here = _os.path.dirname(_os.path.abspath(__file__))
    if here.startswith("/mount/src") or here.startswith("/app"):
        return True   # Streamlit Community Cloud / many PaaS containers
    if _os.environ.get("STREAMLIT_RUNTIME_ENV") == "cloud":
        return True
    return False


IS_CLOUD = _detect_cloud()
_ICON = _os.path.join(_ASSETS, "shredder_icon.png")


def _icon_data_uri(fname):
    """Base64 data URI for an asset image (for inline <img> in the masthead)."""
    path = _os.path.join(_ASSETS, fname)
    if _os.path.exists(path):
        with open(path, "rb") as f:
            return "data:image/png;base64," + _b64.b64encode(f.read()).decode()
    return ""


st.set_page_config(page_title="Project Shredder", layout="wide",
                   page_icon=(_ICON if _os.path.exists(_ICON) else "🎸"),
                   initial_sidebar_state="expanded")


def _inject_pwa():
    """Make the app installable on a phone ('Add to Home Screen' -> app icon,
    full-screen standalone). Builds a web-app manifest from a base64 icon and
    injects it (plus apple-touch-icon + theme-color) into the PARENT page head
    via a tiny component iframe — the reliable way to reach Streamlit's <head>."""
    import json
    icon = _icon_data_uri("shredder_icon.png") or _icon_data_uri("shredder_icon_128.png")
    if not icon:
        return
    manifest = {
        "name": "Project Shredder", "short_name": "Shredder",
        "description": "Live-draft copilot — VORP + edge engine + opponent DNA.",
        "display": "standalone", "orientation": "portrait",
        "background_color": "#0b0d12", "theme_color": "#0b0d12",
        "icons": [
            {"src": icon, "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": icon, "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    manifest_js = json.dumps(json.dumps(manifest))   # embed as a JS string literal
    icon_js = json.dumps(icon)
    _html = f"""
<script>
(function(){{
  try{{
    var doc = window.parent.document;
    // web-app manifest (as a blob URL so no served file is needed)
    var blob = new Blob([{manifest_js}], {{type:'application/manifest+json'}});
    var url = URL.createObjectURL(blob);
    function setLink(rel, href, extra){{
      var l = doc.querySelector('link[rel="'+rel+'"]') || doc.createElement('link');
      l.setAttribute('rel', rel); l.setAttribute('href', href);
      if(extra){{ for(var k in extra) l.setAttribute(k, extra[k]); }}
      doc.head.appendChild(l);
    }}
    setLink('manifest', url);
    setLink('apple-touch-icon', {icon_js});
    function setMeta(name, content){{
      var m = doc.querySelector('meta[name="'+name+'"]') || doc.createElement('meta');
      m.setAttribute('name', name); m.setAttribute('content', content);
      doc.head.appendChild(m);
    }}
    setMeta('theme-color', '#0b0d12');
    setMeta('apple-mobile-web-app-capable', 'yes');
    setMeta('apple-mobile-web-app-status-bar-style', 'black-translucent');
    setMeta('apple-mobile-web-app-title', 'Shredder');
    setMeta('mobile-web-app-capable', 'yes');
    setMeta('viewport', 'width=device-width, initial-scale=1, viewport-fit=cover');
  }}catch(e){{ /* PWA polish is best-effort; never block the app */ }}
}})();
</script>
"""
    # A component iframe is required to EXECUTE the injection script (st.html
    # sanitizes <script>). Prefer the current API; fall back across versions.
    try:
        import streamlit.components.v1 as _components
        _components.html(_html, height=0, width=0)
    except Exception:  # noqa: BLE001
        try:
            st.components.v1.html(_html, height=0, width=0)
        except Exception:  # noqa: BLE001
            pass


_inject_pwa()

st.markdown("""
<style>
:root{
  --bg:#0b0d12; --panel:#141822; --panel2:#1b2130; --line:#2a3242;
  --txt:#eef2f8; --dim:#8b95a7; --accent:#33d69f; --accent2:#5b9dff;
  --warn:#ffb454; --bad:#ff5c5c; --good:#33d69f;
}
.stApp{background:radial-gradient(1100px 600px at 80% -10%, #17202e 0%, var(--bg) 60%);}
[data-testid="stMetricValue"]{font-size:22px;font-weight:800;color:var(--txt);}
[data-testid="stMetricLabel"]{color:var(--dim);text-transform:uppercase;
  font-size:11px;letter-spacing:.5px;}
[data-testid="stVerticalBlockBorderWrapper"]{background:var(--panel);
  border:1px solid var(--line)!important;border-radius:10px;}
.stButton>button{border-radius:8px;font-weight:700;border:1px solid var(--line);
  background:var(--panel2);color:var(--txt);}
.stButton>button:hover{border-color:var(--accent);color:var(--accent);}
.stButton>button[kind="primary"]{background:var(--accent);color:#06231a;
  border:1px solid var(--accent);}
.pill{font:800 12px 'JetBrains Mono',monospace;padding:2px 8px;border-radius:6px;
  background:var(--panel2);color:var(--txt);border:1px solid var(--line);}
.pos-RB{background:#1f3a2e;color:#7ff0c0;border-color:#2f6e52;}
.pos-WR{background:#1f2f4a;color:#8fbcff;border-color:#2f5aa0;}
.pos-QB{background:#3a2f1f;color:#ffcf8f;border-color:#7a5f2f;}
.pos-TE{background:#331f3a;color:#e0a0ff;border-color:#6a2f7a;}
.pos-K,.pos-DST{background:var(--panel2);color:var(--dim);}
.pname{font-weight:800;font-size:16px;color:var(--txt);}
.pmeta{color:var(--dim);font-size:12px;font-family:'JetBrains Mono',monospace;}
.bdg{display:inline-block;font:700 10px/1.5 'JetBrains Mono',monospace;
  padding:2px 7px;margin:2px 3px 2px 0;border-radius:6px;border:1px solid var(--line);
  color:var(--dim);background:var(--panel2);text-transform:uppercase;letter-spacing:.3px;}
.bdg-val{background:#12301f;color:#6fe0a0;border-color:#2f6e52;}
.bdg-urgent{background:#3a1414;color:#ff9c9c;border-color:#7a2f2f;}
.bdg-need{background:#12233a;color:#8fbcff;border-color:#2f5aa0;}
.bdg-cliff{background:#33260f;color:#ffcf8f;border-color:#7a5f2f;}
.hero{background:linear-gradient(100deg,#10261f,#122033);border:1px solid var(--accent);
  border-radius:14px;padding:18px 22px;margin-bottom:14px;
  box-shadow:0 0 30px rgba(51,214,159,.18);}
.hero .tag{font:800 12px 'JetBrains Mono',monospace;color:var(--accent);
  letter-spacing:1.5px;}
.hero .nm{font-size:30px;font-weight:800;color:var(--txt);margin:2px 0;}
.masthead{display:flex;align-items:center;gap:14px;
  background:linear-gradient(100deg,#0d1119 0%,#141a26 100%);
  border:1px solid var(--line);border-left:4px solid var(--accent);
  border-radius:12px;padding:14px 20px;margin-bottom:14px;
  box-shadow:0 6px 30px rgba(0,0,0,.5);}
.masthead img{width:48px;height:48px;border-radius:10px;border:1px solid var(--line);}
.masthead h1{font-size:26px;font-weight:800;margin:0;letter-spacing:1px;
  text-transform:uppercase;color:var(--txt);}
.masthead .sub{color:var(--dim);font-size:12px;margin-top:2px;
  font-family:'JetBrains Mono',monospace;letter-spacing:.3px;}
.rowline{padding:6px 0;border-bottom:1px solid var(--line);}
.slotchip{display:inline-block;font:700 11px 'JetBrains Mono',monospace;
  padding:3px 8px;margin:2px;border-radius:6px;border:1px solid var(--line);}
.slot-filled{background:#12301f;color:#7ff0c0;border-color:#2f6e52;}
.slot-open{background:var(--panel2);color:var(--dim);border-style:dashed;}
.rankhdr{display:flex;gap:8px;font:800 10px 'JetBrains Mono',monospace;
  color:var(--dim);letter-spacing:.6px;text-transform:uppercase;
  border-bottom:1px solid var(--line);padding:4px 0 6px;margin-bottom:2px;}
.rk{font:800 15px 'JetBrains Mono',monospace;color:var(--dim);border-radius:6px;
  padding:2px 0;}
.vr{font:800 11px 'JetBrains Mono',monospace;padding:2px 8px;border-radius:6px;
  border:1px solid var(--line);}
.vr-value{background:#12301f;color:#6fe0a0;border-color:#2f6e52;}
.vr-reach{background:#3a1414;color:#ff9c9c;border-color:#7a2f2f;}
.vr-fair{background:var(--panel2);color:var(--dim);}
.mine-tag{font:800 9px 'JetBrains Mono',monospace;background:var(--accent);
  color:#06231a;border-radius:4px;padding:1px 6px;margin-left:4px;}
.gone-tag{font:800 9px 'JetBrains Mono',monospace;background:var(--panel2);
  color:var(--dim);border:1px solid var(--line);border-radius:4px;padding:1px 6px;
  margin-left:4px;}
/* contextual insight cards */
.insight{border-radius:10px;padding:10px 14px;margin:6px 0;
  border:1px solid var(--line);background:var(--panel2);
  border-left:3px solid var(--accent2);}
.insight.i-cliff{border-left-color:var(--bad);}
.insight.i-stack{border-left-color:var(--accent2);}
.insight.i-combo{border-left-color:var(--accent);}
.insight.i-snipe{border-left-color:var(--warn);}
.insight.i-dark_horse{border-left-color:#e0a0ff;}
.insight .it{font:800 13px 'Inter',sans-serif;color:var(--txt);}
.insight .ib{font-size:12.5px;color:var(--dim);margin-top:2px;line-height:1.45;}

/* ======================= MOBILE / PHONE (<=820px) ======================= */
@media (max-width: 820px){
  /* reclaim the huge default page padding on a phone */
  .block-container{padding:0.6rem 0.7rem 4rem 0.7rem !important; max-width:100% !important;}
  /* STACK columns vertically — Streamlit horizontal columns become slivers on
     a phone; force each to full width so the board reads top-to-bottom */
  [data-testid="stHorizontalBlock"]{flex-direction:column !important; gap:0.35rem !important;}
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
  [data-testid="stHorizontalBlock"] > [data-testid="column"]{
    width:100% !important; flex:1 1 100% !important; min-width:0 !important;}
  /* thumb-friendly buttons */
  .stButton>button{min-height:46px !important; font-size:15px !important;
    border-radius:10px !important;}
  /* bigger form controls for touch (search box, selects, number inputs) */
  [data-baseweb="select"] div, [data-baseweb="input"] input,
  .stNumberInput input, .stSelectbox div{font-size:16px !important;}
  /* hero card scales down but stays bold */
  .hero{padding:14px 16px !important;}
  .hero .nm{font-size:23px !important;}
  /* metric tiles shrink so 4-across becomes a tidy stacked list */
  [data-testid="stMetricValue"]{font-size:19px !important;}
  [data-testid="stMetricLabel"]{font-size:10px !important;}
  /* masthead: smaller so it doesn't eat the first screen */
  .masthead{padding:10px 14px !important; gap:10px !important;}
  .masthead h1{font-size:20px !important;}
  .masthead .sub{font-size:10px !important;}
  .masthead img{width:38px !important; height:38px !important;}
  /* tables/rows: tighter, wrap-friendly */
  .pname{font-size:15px !important;}
  .pmeta{font-size:12px !important;}
  .bdg{font-size:10px !important;}
  /* sidebar is a drawer on mobile; widen its controls */
  section[data-testid="stSidebar"]{min-width:88vw !important;}
  /* keep the phone-only hint visible, hide the desktop-only one */
  .only-desktop{display:none !important;}
}
@media (min-width: 821px){ .only-mobile{display:none !important;} }
</style>
""", unsafe_allow_html=True)

SCORING_LABELS = {"Standard (non-PPR)": "std", "Half PPR": "half", "Full PPR": "ppr"}
_POS_ORDER = ["QB", "RB", "WR", "TE", "FLEX", "DST", "K"]


# --------------------------------------------------------------------------- state
def _init_state():
    ss = st.session_state
    ss.setdefault("drafted", set())          # set of player names off the board
    ss.setdefault("my_roster", [])           # [(name, position)] — my team
    ss.setdefault("current_overall", 1)      # overall pick on the clock (1-indexed)
    ss.setdefault("team_rosters", {})        # {slot: [names]} — everyone
    ss.setdefault("pick_log", [])            # [(overall, name, pos, slot)]
    ss.setdefault("undo_stack", [])          # snapshots for undo
    ss.setdefault("mode", "Mock")
    ss.setdefault("mock_on", False)
    ss.setdefault("espn", None)              # EspnClient or None
    ss.setdefault("espn_status", "")
    ss.setdefault("espn_s2", "")
    ss.setdefault("espn_swid", "")
    ss.setdefault("sync_note", "")
    ss.setdefault("_last_trash", "")
    ss.setdefault("copilot_voice", True)
    ss.setdefault("_mgr_dna", {})            # {owner_id: dna dossier} from past drafts
    ss.setdefault("_slot_to_owner", {})      # {slot: (owner_id, name)} this season
    ss.setdefault("_dna_note", "")           # status line after learning DNA
    ss.setdefault("dna_seasons", "2021,2022,2023,2024,2025")
    ss.setdefault("_manual_tendencies", {})  # {slot: {tendencies, rookie_averse}}
    ss.setdefault("_cookie_loaded", False)
    if not ss._cookie_loaded:
        if IS_CLOUD:
            # MULTI-USER CLOUD: each visitor logs in with THEIR OWN cookies, held
            # only in their browser session. We do NOT auto-load anything here —
            # not the shared cookie file (would be another user's login) and not
            # a single-owner st.secrets set — so everyone starts blank and pastes
            # their own. (A single-owner deploy can still force cookies by setting
            # the OWNER_COOKIES secret; off by default for the public app.)
            try:
                if hasattr(st, "secrets") and st.secrets.get("owner_cookies"):
                    _sec = st.secrets.get("espn", {})
                    ss.espn_s2 = _sec.get("espn_s2", "") or st.secrets.get("espn_s2", "")
                    ss.espn_swid = _sec.get("swid", "") or st.secrets.get("swid", "")
                    if ss.espn_s2 or ss.espn_swid:
                        ss["_cookie_source"] = "secrets"
            except Exception:  # noqa: BLE001
                pass
        else:
            # DESKTOP (unchanged): saved cookie file, then browser auto-read.
            if SEC is not None:
                try:
                    s2, swid, _src = SEC.auto_load()
                    if s2 or swid:
                        ss.espn_s2, ss.espn_swid = s2, swid
                        ss["_cookie_source"] = _src
                except Exception:  # noqa: BLE001
                    pass
        ss._cookie_loaded = True


_init_state()
ss = st.session_state


def _saved_leagues_load():
    """Saved leagues list. On the DESKTOP this is the shared file (SL.load); on
    the multi-user CLOUD it's per-session (st.session_state) so one visitor's
    discovered leagues never show up for another."""
    if IS_CLOUD:
        return list(st.session_state.get("_my_leagues", []))
    try:
        return SL.load() if SL else []
    except Exception:  # noqa: BLE001
        return []


def _saved_leagues_upsert(leagues):
    """Store discovered leagues. Cloud -> session only; desktop -> shared file."""
    if IS_CLOUD:
        cur = {int(e["league_id"]): e for e in st.session_state.get("_my_leagues", [])
               if str(e.get("league_id")).isdigit()}
        for d in leagues or []:
            if str(d.get("league_id")).isdigit():
                cur[int(d["league_id"])] = d
        st.session_state["_my_leagues"] = list(cur.values())
        return
    try:
        if SL:
            SL.bulk_upsert(leagues)
    except Exception:  # noqa: BLE001
        pass


def _password_gate():
    """Gate the app behind a password WHEN one is configured in st.secrets
    (i.e. on the public cloud deploy). If no password is set — the normal
    DESKTOP case — this is a no-op and the app runs exactly as before."""
    try:
        want = (st.secrets.get("app_password", "")
                or st.secrets.get("password", "")) if hasattr(st, "secrets") else ""
    except Exception:  # noqa: BLE001
        want = ""
    if not want:
        return  # no password configured -> desktop / open, unchanged
    if ss.get("_authed"):
        return
    # simple full-screen login
    st.markdown("## 🎸 Project Shredder")
    st.caption("Enter the access password to continue.")
    with st.form("pw_gate", clear_on_submit=False):
        pw = st.text_input("Password", type="password")
        ok = st.form_submit_button("Enter", type="primary")
    if ok:
        if pw == want:
            ss["_authed"] = True
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()


_password_gate()


@st.cache_data(show_spinner="Loading player projections…")
def _load_pool():
    return P.load_players(prefer_live=True)


pool = _load_pool()
name_to_raw = {p.name: p for p in pool}


def _render_masthead():
    """PROJECT SHREDDER masthead — the icon + name + tagline, rendered once up top."""
    uri = _icon_data_uri("shredder_icon_128.png") or _icon_data_uri("shredder_icon.png")
    img = f'<img src="{uri}" alt="Shredder">' if uri else '<span style="font-size:40px;">🎸</span>'
    st.markdown(
        f'<div class="masthead">{img}<div>'
        f'<h1>PROJECT SHREDDER</h1>'
        f'<div class="sub">// live-draft copilot · VORP + edge engine · '
        f'contextual insights · shred the board</div>'
        f'</div></div>', unsafe_allow_html=True)


_render_masthead()


@st.cache_data(show_spinner="Building rankings…")
def _rankings_cached(scoring_key, teams, rounds, bench, starters_key, npool):
    """Full-pool value ranking. Cached on the league shape (not on drafted state)
    so the ranks stay STABLE as a reference board — the UI crosses off drafted
    players at render time. `npool` in the key busts the cache if the pool
    reloads (live vs seed)."""
    _cfg = E.LeagueConfig(
        teams=int(teams), draft_slot=1, rounds=int(rounds),
        scoring=E.Scoring.preset(scoring_key),
        starters=dict(starters_key), bench=int(bench))
    return SR.build_rankings(pool, _cfg, scoring_key, top_n=max(300, npool))


# --------------------------------------------------------------------------- helpers
def _snapshot():
    return {
        "drafted": set(ss.drafted), "my_roster": list(ss.my_roster),
        "current_overall": ss.current_overall, "pick_log": list(ss.pick_log),
        "team_rosters": copy.deepcopy(ss.team_rosters),
    }


def _push_undo():
    ss.undo_stack.append(_snapshot())
    ss.undo_stack[:] = ss.undo_stack[-40:]


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


def _rankings_cache(scoring_key):
    """Build (or fetch cached) full-pool rankings for the current league shape."""
    return _rankings_cached(
        scoring_key, int(teams), int(rounds), int(bench),
        tuple(sorted((p, int(v)) for p, v in starters.items() if v)), len(pool))


def _restore_player(name):
    """Put a crossed-off player back on the board (remove from drafted / rosters /
    my_roster / pick_log). Used by the rankings guide's Undo button."""
    _push_undo()
    ss.drafted.discard(name)
    ss.my_roster = [(n, p) for n, p in ss.my_roster if n != name]
    for slot_id, names in list(ss.team_rosters.items()):
        ss.team_rosters[slot_id] = [n for n in names if n != name]
    ss.pick_log = [row for row in ss.pick_log if row[1] != name]


def _record_pick(cfg, name, position, mine, opps, advance=True):
    """Log one pick and update all rosters. If `mine`, it joins my team. In mock
    mode, after MY pick the bots draft forward to my next turn automatically."""
    if name in ss.drafted:
        return
    # HARD STOP at the end of the draft: teams * rounds picks total. Without this
    # the board kept accepting picks into a phantom 17th round (Your Players 17 in
    # a 16-round league) and the late-round gates (dark horse fires near your LAST
    # pick) never resolved. Once complete, no more picks are recorded.
    _total = int(cfg.teams) * int(cfg.rounds)
    if int(ss.current_overall) > _total:
        return
    _push_undo()
    ov = int(ss.current_overall)
    slot = O._snake_slot(ov, int(cfg.teams))
    ss.pick_log.append((ov, name, position, slot))
    ss.team_rosters.setdefault(slot, []).append(name)
    ss.drafted.add(name)
    if mine:
        ss.my_roster.append((name, position))
        # Shredder attitude: a cocky one-liner to paste in the league chat,
        # stashed so it survives the rerun and shows on the board.
        ss["_last_trash"] = CO.trash_talk(name)
    ss.current_overall = ov + 1
    if advance and mine and ss.get("mock_on") and ss.current_overall <= _total:
        r = MOCK.bots_pick_until_me(pool, cfg, ss.drafted, ss.team_rosters,
                                    int(ss.current_overall), opponents=opps,
                                    pick_log=ss.pick_log)
        ss.current_overall = r["now_overall"]
    # never let the counter run past the final pick + 1 (the 'complete' sentinel)
    if ss.current_overall > _total:
        ss.current_overall = _total + 1


def _mark_gone(cfg, name, opps):
    """Someone else drafted `name`. Take them off the board (position pulled from
    the pool) and requeue — the board recomputes on the next run automatically."""
    raw = name_to_raw.get(name)
    pos = raw.position if raw else "?"
    _record_pick(cfg, name, pos, mine=False, opps=opps, advance=False)


def _reset_board():
    ss.drafted = set(); ss.my_roster = []; ss.team_rosters = {}
    ss.pick_log = []; ss.undo_stack = []; ss.current_overall = 1


def _start_mock(cfg, opps):
    _reset_board()
    ss.mock_on = True
    r = MOCK.bots_pick_until_me(pool, cfg, ss.drafted, ss.team_rosters,
                                int(ss.current_overall), opponents=opps,
                                pick_log=ss.pick_log)
    ss.current_overall = r["now_overall"]


def _learn_dna(league_id, season, seasons_txt):
    """Learn each manager's draft DNA from past seasons and map it to this
    year's slots. Populates ss._mgr_dna + ss._slot_to_owner so the survival
    model + prophecy know how each specific opponent tends to draft. Best-effort:
    on any failure it leaves the defaults in place."""
    if LH is None:
        ss._dna_note = "Opponent DNA unavailable (league_history not loaded)."
        return
    try:
        yrs = [int(y) for y in str(seasons_txt).replace(" ", "").split(",")
               if y.strip().isdigit()]
    except Exception:  # noqa: BLE001
        yrs = []
    if not yrs:
        ss._dna_note = "No past seasons given — opponents use default tendencies."
        return
    try:
        drafts = LH.pull_past_drafts(int(league_id), yrs, ss.espn_s2, ss.espn_swid)
        if not drafts:
            ss._mgr_dna, ss._slot_to_owner = {}, {}
            ss._dna_note = ("No draft history found for those seasons — opponents "
                            "use default tendencies.")
            return
        # DEEP analysis: context-normalized, recency-weighted, confidence-scored,
        # scoring-change aware, and POOLED ACROSS YOUR OTHER LEAGUES for any
        # manager you share more than one league with (bigger sample = stronger
        # read). Falls back to the simpler learner if unavailable.
        try:
            import manager_analysis as MA
            # gather every OTHER saved league you can read, to pool shared managers
            league_drafts = {int(league_id): drafts}
            league_ctx = {int(league_id):
                          LH.season_contexts(int(league_id), yrs,
                                             ss.espn_s2, ss.espn_swid)}
            try:
                other = [int(e["league_id"]) for e in (SL.load() if SL else [])
                         if str(e.get("league_id")).isdigit()
                         and int(e["league_id"]) != int(league_id)]
            except Exception:  # noqa: BLE001
                other = []
            _lg_names = {}
            try:
                _lg_names = {int(e["league_id"]):
                             (e.get("league_name") or e.get("label")
                              or f"League {e['league_id']}")
                             for e in (SL.load() if SL else [])
                             if str(e.get("league_id")).isdigit()}
            except Exception:  # noqa: BLE001
                _lg_names = {}
            for _oid in other[:6]:      # cap to keep connect snappy
                try:
                    _od = LH.pull_past_drafts(_oid, yrs, ss.espn_s2, ss.espn_swid)
                    if _od:
                        league_drafts[_oid] = _od
                        league_ctx[_oid] = LH.season_contexts(_oid, yrs,
                                                              ss.espn_s2, ss.espn_swid)
                except Exception:  # noqa: BLE001
                    pass
            if len(league_drafts) > 1:
                mgr_dna = MA.analyze_across_leagues(league_drafts, league_ctx,
                                                    current_scoring_key=scoring_key,
                                                    league_names=_lg_names)
            else:
                mgr_dna = MA.analyze_managers(
                    drafts, league_ctx[int(league_id)],
                    current_scoring_key=scoring_key)
        except Exception:  # noqa: BLE001
            mgr_dna = LH.learn_dna_by_manager(drafts)
        s2o = LH.current_slot_to_owner(int(league_id), int(season),
                                       ss.espn_s2, ss.espn_swid)
        ss._mgr_dna = mgr_dna
        ss._slot_to_owner = s2o
        learned = sum(1 for d in mgr_dna.values()
                      if d.get("tendencies") != ["ADP-robot"])
        if s2o:
            # DNA is pinned to this year's seats — the full draft-day read is live.
            ss._dna_note = (f"🧬 DNA locked in: {len(mgr_dna)} managers "
                            f"({learned} with a real lean) mapped to their seats "
                            f"across {len(drafts)} past draft(s). Snipes + "
                            f"survival odds now know how each rival drafts.")
        else:
            # Learned the managers, but ESPN hasn't published this year's draft
            # ORDER yet — so we can't attach DNA to seats. Common the day before.
            ss._dna_note = (f"🧬 Learned {len(mgr_dna)} managers' DNA "
                            f"({learned} with a real lean), but ESPN hasn't set "
                            f"this year's DRAFT ORDER yet — so it can't map to "
                            f"seats. Hit '🧬 Re-learn' once the order is posted "
                            f"(usually just before the draft) to light up snipes.")
    except Exception as ex:  # noqa: BLE001
        ss._mgr_dna, ss._slot_to_owner = {}, {}
        ss._dna_note = f"DNA learn failed ({ex}); using default tendencies."


def _apply_dna_to_opponents(opps):
    """Stamp learned manager DNA (tendencies, rookie-aversion, real names) onto
    the opponent profiles by this year's seat, and build the loyalty map prophecy
    uses to predict the ACTUAL player each manager keeps drafting."""
    mgr_dna = ss.get("_mgr_dna") or {}
    s2o = ss.get("_slot_to_owner") or {}
    loyalty_by_slot = {}

    # Manual tendency tags (Mock practice) reapply every run so they survive
    # profile rebuilds. Applied FIRST so learned ESPN DNA can override if present.
    for slot_id, tag in (ss.get("_manual_tendencies") or {}).items():
        prof = opps.profiles.get(int(slot_id)) if hasattr(opps, "profiles") else None
        if prof is not None:
            prof.tendencies = tag.get("tendencies") or ["ADP-robot"]
            prof.rookie_averse = bool(tag.get("rookie_averse"))

    if not mgr_dna or not s2o or LH is None:
        return loyalty_by_slot
    try:
        LH.apply_manager_dna(opps, mgr_dna, s2o)
    except Exception:  # noqa: BLE001
        pass

    def _ln(s):
        s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", (s or "").lower().strip())
        s = re.sub(r"[^a-z0-9 ]", "", s)
        return re.sub(r"\s+", " ", s).strip()

    for slot, owner_tuple in s2o.items():
        owner = owner_tuple[0] if isinstance(owner_tuple, (list, tuple)) else owner_tuple
        d = mgr_dna.get(owner)
        prof = opps.profiles.get(int(slot)) if hasattr(opps, "profiles") else None
        if not d:
            continue
        # real owner name on the seat (so banners say "Marcus" not "slot 4")
        if prof is not None and d.get("manager_name"):
            prof.name = d["manager_name"]
        # loyalty: players this manager drafts repeatedly -> prophecy predicts them
        if d.get("favorite_players"):
            loyalty_by_slot[int(slot)] = {_ln(nm): c
                                          for nm, c in d["favorite_players"].items()}
    return loyalty_by_slot


def _sync_espn(cfg, force=False):
    """Pull the live draft state from ESPN and fold every pick into the board."""
    if not ss.espn:
        return
    try:
        state = ss.espn.draft_state()
    except Exception as ex:  # noqa: BLE001
        ss.sync_note = f"ESPN poll failed (kept last state): {ex}"
        return
    if getattr(state, "complete", False) and not getattr(state, "in_progress", False) \
            and not force:
        ss.sync_note = ("Finished draft detected — not auto-loading it. "
                        "Hit Sync now to review it, or start a Mock instead.")
        return
    pool_by_norm = {_norm(p.name): p.name for p in pool}
    my_slot = int(cfg.draft_slot)
    teams = int(cfg.teams)
    matched, unmatched = 0, []
    # rebuild from scratch so ESPN is the source of truth for the live board
    drafted, my_roster, team_rosters, pick_log = set(), [], {}, []
    for pk in sorted(state.picks, key=lambda x: getattr(x, "overall", 0) or 0):
        nm = pk.player_name
        if not nm:
            continue
        canon = pool_by_norm.get(_norm(nm), nm)
        ov = getattr(pk, "overall", 0) or (len(pick_log) + 1)
        slot = O._snake_slot(ov, teams)
        pos = pk.position or (name_to_raw[canon].position
                              if canon in name_to_raw else "?")
        drafted.add(canon)
        team_rosters.setdefault(slot, []).append(canon)
        pick_log.append((ov, canon, pos, slot))
        if slot == my_slot:
            my_roster.append((canon, pos))
        matched += 1
        if _norm(nm) not in pool_by_norm:
            unmatched.append(nm)
    ss.drafted = drafted
    ss.my_roster = my_roster
    ss.team_rosters = team_rosters
    ss.pick_log = sorted(pick_log)
    ss.current_overall = len(pick_log) + 1
    ss.sync_note = (f"Synced {matched} picks from ESPN."
                    + (f"  ({len(unmatched)} names not in pool: "
                       + ", ".join(unmatched[:4]) + "…)" if unmatched else ""))


def _badge_class(b: str) -> str:
    u = b.upper()
    if "VALUE +" in u:
        return "bdg-val"
    if "WON'T LAST" in u or "REACH" in u or "OUT" in u:
        return "bdg-urgent"
    if "FILLS NEED" in u:
        return "bdg-need"
    if "TIER CLIFF" in u:
        return "bdg-cliff"
    return "bdg"


def _badges_html(badges, limit=4):
    return "".join(f'<span class="bdg {_badge_class(b)}">{b}</span>'
                   for b in badges[:limit])


def _need_summary(rstate) -> str:
    """One plain-English line about what the roster still needs to start."""
    opens = []
    for p in ("QB", "RB", "WR", "TE", "DST", "K"):
        n = rstate["starter_open"].get(p, 0)
        if n > 0:
            opens.append(f"{n}×{p}")
    if rstate["flex_open"] > 0:
        opens.append(f"{rstate['flex_open']}×FLEX")
    if not opens:
        return "Starting lineup is full — now drafting the best bench value."
    return "Still need to start: " + ", ".join(opens) + "."


def _render_roster_panel(rstate, cfg):
    """Your starting lineup, filling up slot by slot, then your bench."""
    st.markdown("#### Your lineup")
    counts = dict(rstate["counts"])
    filled_chips = []
    # dedicated starter slots
    for pos in ("QB", "RB", "WR", "TE", "DST", "K"):
        for i in range(cfg.starters.get(pos, 0)):
            if counts.get(pos, 0) > 0:
                counts[pos] -= 1
                filled_chips.append((pos, True))
            else:
                filled_chips.append((pos, False))
    # flex slots — fill from leftover RB/WR/TE
    for i in range(rstate["flex_slots"]):
        took = None
        for p in ("RB", "WR", "TE"):
            if counts.get(p, 0) > 0:
                counts[p] -= 1
                took = p
                break
        filled_chips.append(("FLEX" if took is None else f"FLEX·{took}",
                             took is not None))
    html = ""
    for label, filled in filled_chips:
        cls = "slot-filled" if filled else "slot-open"
        html += f'<span class="slotchip {cls}">{label}</span>'
    st.markdown(f'<div>{html}</div>', unsafe_allow_html=True)

    # the actual players, grouped
    if ss.my_roster or any(ss.team_rosters.get(int(cfg.draft_slot), [])):
        st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
        mine = list(ss.my_roster)
        seen = {n for n, _ in mine}
        for nm in ss.team_rosters.get(int(cfg.draft_slot), []):
            if nm not in seen:
                raw = name_to_raw.get(nm)
                if raw:
                    mine.append((nm, raw.position))
        by_pos = {}
        for nm, pos in mine:
            by_pos.setdefault(pos, []).append(nm)
        for pos in ("QB", "RB", "WR", "TE", "DST", "K"):
            for nm in by_pos.get(pos, []):
                st.markdown(
                    f'<div class="rowline"><span class="pill pos-{pos}">{pos}</span> '
                    f'<span class="pname" style="font-size:14px;">{nm}</span></div>',
                    unsafe_allow_html=True)
    else:
        st.caption("No players yet — draft your first pick.")


def _render_why(rec, key):
    """The 'why this pick' breakdown — an itemized, plain-English view of every
    factor that went into this player's score, straight from the engine."""
    with st.expander("🔍 Why this pick? — full breakdown", expanded=False):
        # context rows (value is None) first, then scored factors
        ctx = [e for e in rec.explain if e.get("value") is None]
        scored = [e for e in rec.explain if e.get("value") is not None]
        if ctx:
            st.markdown("**The data**")
            for e in ctx:
                st.markdown(
                    f'<div class="pmeta" style="margin:2px 0;">'
                    f'<b style="color:var(--txt);">{e["label"]}:</b> {e["detail"]}'
                    f'</div>', unsafe_allow_html=True)
        if scored:
            st.markdown("**How the score adds up**")
            for e in scored:
                v = e["value"]
                col = "var(--good)" if v > 0 else ("var(--bad)" if v < 0 else "var(--dim)")
                sign = "+" if v > 0 else ""
                st.markdown(
                    f'<div style="display:flex;gap:10px;align-items:baseline;'
                    f'margin:3px 0;border-bottom:1px solid var(--line);padding-bottom:3px;">'
                    f'<span style="font-family:JetBrains Mono,monospace;font-weight:800;'
                    f'color:{col};min-width:56px;text-align:right;">{sign}{v:g}</span>'
                    f'<span><b style="color:var(--txt);">{e["label"]}</b> '
                    f'<span class="pmeta">— {e["detail"]}</span></span></div>',
                    unsafe_allow_html=True)
            total = sum(e["value"] for e in scored)
            st.markdown(
                f'<div style="margin-top:6px;font-family:JetBrains Mono,monospace;'
                f'font-weight:800;color:var(--accent);">= {total:g} composite '
                f'score</div>', unsafe_allow_html=True)
        if rec.injury_note:
            st.info(f"🏥 {rec.injury_note}")


def _render_insights(insights, cfg, opps):
    """Render the contextual insight cards that popped up for this pick. Each
    can carry a one-click Draft button for the player it suggests."""
    if not insights:
        return
    for i, ins in enumerate(insights):
        st.markdown(
            f'<div class="insight i-{ins["kind"]}">'
            f'<div class="it">{ins["icon"]} {ins["title"]}</div>'
            f'<div class="ib">{ins["body"]}</div></div>',
            unsafe_allow_html=True)
        player = ins.get("player")
        if player and player not in ss.drafted:
            bc = st.columns([1, 1, 4])
            if bc[0].button(f"✓ Draft {player.split()[-1]}",
                            key=f"ins_draft_{ins['kind']}_{i}",
                            use_container_width=True):
                _record_pick(cfg, player, ins.get("position") or "?",
                             mine=True, opps=opps)
                st.rerun()
            if bc[1].button("✗ Gone", key=f"ins_gone_{ins['kind']}_{i}",
                            use_container_width=True):
                _mark_gone(cfg, player, opps)
                st.rerun()


def _grade_team(players, cfg, scoring_key):
    """Grade one drafted team A+..F on (a) total projected value and (b) roster
    construction (did they fill starters without wasting picks). `players` =
    list of (name, position). Returns (letter, score0_100, note)."""
    import engine as E
    counts = {}
    total_proj = 0.0
    for nm, pos in players:
        counts[pos] = counts.get(pos, 0) + 1
        raw = name_to_raw.get(nm)
        if raw:
            total_proj += E.project_points(raw.stats, cfg.scoring)
    s = cfg.starters

    # construction score: reward covering each starting slot, penalize gaps and
    # extreme hoarding (e.g. 4 QBs) that wasted picks.
    con = 100.0
    need = {"QB": s.get("QB", 1), "RB": s.get("RB", 2), "WR": s.get("WR", 2),
            "TE": s.get("TE", 1), "DST": s.get("DST", 1), "K": s.get("K", 1)}
    for p, want in need.items():
        have = counts.get(p, 0)
        if have < want:
            con -= 18 * (want - have)          # missing a starter hurts a lot
    # flex coverage (need at least starters + flex worth of RB/WR/TE)
    flex_need = s.get("RB", 2) + s.get("WR", 2) + s.get("TE", 1) + s.get("FLEX", 1)
    if counts.get("RB", 0) + counts.get("WR", 0) + counts.get("TE", 0) < flex_need:
        con -= 12
    # hoarding single-slot positions = wasted capital
    for p in ("QB", "TE", "DST", "K"):
        over = counts.get(p, 0) - max(1, s.get(p, 1)) - 1
        if over > 0:
            con -= 6 * over
    con = max(0.0, min(100.0, con))

    return total_proj, con, counts


def _render_draft_summary(cfg, scoring_key):
    """End-of-draft board: every team graded A+..F, yours highlighted. Grades
    blend total projected points (value) with roster construction (did you build
    a legal, balanced starting lineup)."""
    st.success("🏁 **Draft complete!** Here's how every team's roster grades out.")

    teams_n = int(cfg.teams)
    # collect each team's players from team_rosters (slot -> [names])
    graded = []
    for slot in range(1, teams_n + 1):
        names = ss.team_rosters.get(slot, [])
        players = [(nm, name_to_raw[nm].position) for nm in names
                   if nm in name_to_raw]
        proj, con, counts = _grade_team(players, cfg, scoring_key)
        graded.append({"slot": slot, "players": players, "proj": proj,
                       "con": con, "counts": counts})

    # normalize projected points to 0-100 across the league (relative value)
    projs = [g["proj"] for g in graded] or [0]
    lo, hi = min(projs), max(projs)
    span = (hi - lo) or 1.0
    for g in graded:
        value_score = 100.0 * (g["proj"] - lo) / span
        # blend: 60% value vs the room, 40% construction
        g["score"] = round(0.6 * value_score + 0.4 * g["con"], 1)

    def _letter(score):
        for cut, lt in [(93, "A+"), (88, "A"), (83, "A-"), (78, "B+"), (73, "B"),
                        (68, "B-"), (63, "C+"), (58, "C"), (53, "C-"),
                        (45, "D"), (0, "F")]:
            if score >= cut:
                return lt
        return "F"

    # rank best-to-worst
    graded.sort(key=lambda g: g["score"], reverse=True)
    my_slot = int(cfg.draft_slot)

    for rank, g in enumerate(graded, 1):
        mine = g["slot"] == my_slot
        who = (opps.profiles[g["slot"]].name
               if g["slot"] in opps.profiles and opps.profiles[g["slot"]].name
               else f"Team (slot {g['slot']})")
        letter = _letter(g["score"])
        mix = " · ".join(f"{p}{g['counts'].get(p,0)}"
                         for p in ("QB", "RB", "WR", "TE", "DST", "K")
                         if g["counts"].get(p, 0))
        border = "var(--accent)" if mine else "var(--line)"
        tag = ' <span class="mine-tag">YOU</span>' if mine else ""
        with st.container(border=True):
            c = st.columns([0.7, 3.4, 1.0])
            c[0].markdown(f'<div style="font:800 26px \'JetBrains Mono\',monospace;'
                          f'color:var(--accent);text-align:center;">{letter}</div>',
                          unsafe_allow_html=True)
            c[1].markdown(
                f'<div><b>#{rank} · {who}</b>{tag}</div>'
                f'<div class="pmeta">{mix}</div>', unsafe_allow_html=True)
            c[2].markdown(f'<div class="pmeta" style="text-align:right;">score '
                          f'{g["score"]:.0f}<br>proj {g["proj"]:.0f}</div>',
                          unsafe_allow_html=True)
            if mine:
                with st.expander("Your full roster"):
                    for nm, pos in sorted(g["players"], key=lambda x: x[1]):
                        st.markdown(f'<span class="pill pos-{pos}">{pos}</span> '
                                    f'{nm}', unsafe_allow_html=True)

    st.divider()
    if st.button("🔄 Start a new draft"):
        _reset_board()
        st.rerun()


def _render_rivals_forecast(cfg, opps, loyalty_by_slot):
    """Crystal ball: predict what each opponent between now and your next pick
    will take, and flag which top players are likely to SURVIVE to you. This is
    the 'who's left for me' view, powered by opponent DNA + loyalty."""
    ovn = int(ss.current_overall)
    nxt = next((p for p in cfg.my_overall_picks() if p > ovn), None)
    if nxt is None or nxt <= ovn:
        return
    try:
        preds = PROPH.predict_board(pool, cfg, set(ss.drafted), ovn,
                                    opponents=opps, scoring_key=scoring_key,
                                    horizon=int(cfg.teams) * 2,
                                    loyalty_by_slot=loyalty_by_slot or {})
    except Exception:  # noqa: BLE001
        return
    between = [p for p in preds if not p.is_me and ovn <= p.overall < nxt]
    if not between:
        return
    dna_on = bool(ss.get("_mgr_dna"))
    label = ("using each seat's learned DNA" if dna_on
             else "using ADP + default tendencies (connect ESPN + learn DNA for "
                  "real per-manager reads)")
    # slot -> deep profile (for confidence + scouting read on each rival)
    _mgr = ss.get("_mgr_dna") or {}
    _s2o = ss.get("_slot_to_owner") or {}
    _slot_prof = {}
    for _sl, _ot in _s2o.items():
        _own = _ot[0] if isinstance(_ot, (list, tuple)) else _ot
        if _mgr.get(_own):
            _slot_prof[int(_sl)] = _mgr[_own]

    with st.expander(f"🔮 Who's left for you? — {len(between)} picks until you're up "
                     f"({label})", expanded=False):
        # 1) predicted picks before you
        st.markdown("**Rivals on the clock before you**")
        likely_gone = set()
        for p in between:
            if not p.top:
                continue
            who = (opps.profiles[p.slot].name
                   if p.slot in opps.profiles and opps.profiles[p.slot].name
                   else f"slot {p.slot}")
            nm, pos, conf = p.top[0]
            likely_gone.add(nm)
            alts = " / ".join(f"{n} ({ps})" for n, ps, _ in p.top[1:3])
            # deep scouting chip: confidence-graded read of this manager
            _dp = _slot_prof.get(p.slot)
            scout = ""
            if _dp:
                cl = _dp.get("confidence_label", "")
                lean = ", ".join(t for t in _dp.get("tendencies", [])
                                 if t != "ADP-robot")[:40] or "balanced"
                qf = _dp.get("qb_first_round")
                qbnote = (f" · QB early (~r{qf:.0f})" if qf and qf <= 5
                          else f" · streams QB (~r{qf:.0f})" if qf else "")
                pooled = (" · 🔗pooled" if _dp.get("cross_league") else "")
                scout = (f'<br/><span style="color:var(--dim);font-size:11px;">'
                         f'   ↳ {cl} read: {lean}{qbnote}{pooled}</span>')
            st.markdown(
                f'<div class="rowline pmeta">#{p.overall} · <b style="color:var(--txt);">'
                f'{who}</b> → likely <b style="color:var(--warn);">{nm}</b> '
                f'<span class="pill pos-{pos}" style="font-size:10px;">{pos}</span> '
                f'({int(conf*100)}%)' + (f' · else {alts}' if alts else '')
                + scout + '</div>',
                unsafe_allow_html=True)
        # 2) top players predicted to SURVIVE to your next pick
        top_now = [r for r in recs[:14] if r.name not in likely_gone]
        if top_now:
            st.markdown("**Should still be there at your next pick**")
            for r in top_now[:6]:
                st.markdown(
                    f'<div class="rowline pmeta">'
                    f'<span class="pill pos-{r.position}" style="font-size:10px;">'
                    f'{r.position}</span> <b style="color:var(--good);">{r.name}</b> '
                    f'· board #{recs.index(r)+1}'
                    + (f" · {int(r.survival*100)}% survives"
                       if r.survival is not None else "") + '</div>',
                    unsafe_allow_html=True)
            st.caption("So you can grab a player who WON'T last now, and count on "
                       "these being there when you're back up.")


def _render_recent_picks(limit=10):
    if not ss.pick_log:
        return
    st.markdown("#### Recent picks")
    for ov, nm, pos, slot in reversed(ss.pick_log[-limit:]):
        mine = (slot == int(ss.get("_disp_slot", -1)))
        tag = "🟢 you" if nm in {n for n, _ in ss.my_roster} else f"slot {slot}"
        st.markdown(
            f'<div class="rowline pmeta">#{ov} · <span class="pill pos-{pos}" '
            f'style="font-size:10px;">{pos}</span> {nm} · {tag}</div>',
            unsafe_allow_html=True)


# --------------------------------------------------------------------------- sidebar
st.sidebar.title("🎸 SHREDDER")

mode = st.sidebar.radio("How are you drafting?",
                        ["Mock", "Manual", "ESPN"],
                        index=["Mock", "Manual", "ESPN"].index(ss.mode),
                        help="Mock = practice vs bots · Manual = you enter every "
                             "pick · ESPN = sync live from your league.")
ss.mode = mode

st.sidebar.markdown("### League")
scoring_label = st.sidebar.selectbox("Scoring", list(SCORING_LABELS.keys()), index=1)
scoring_key = SCORING_LABELS[scoring_label]
teams = st.sidebar.number_input("Teams", 4, 16, 12)
slot = st.sidebar.number_input("Your draft slot", 1, int(teams), 6)
rounds = st.sidebar.number_input("Rounds", 8, 25, 16)

with st.sidebar.expander("Starting lineup", expanded=False):
    c1, c2 = st.columns(2)
    qb = c1.number_input("QB", 0, 3, 1)
    rb = c2.number_input("RB", 0, 5, 2)
    wr = c1.number_input("WR", 0, 5, 2)
    te = c2.number_input("TE", 0, 3, 1)
    flex = c1.number_input("FLEX", 0, 3, 1)
    dst = c2.number_input("D/ST", 0, 2, 1)
    k = c1.number_input("K", 0, 2, 1)
    bench = c2.number_input("Bench", 0, 12, 7)

prefer_floor = st.sidebar.toggle("Prioritize weekly floor (consistency)", value=False)
ss.copilot_voice = st.sidebar.toggle(
    "🎸 Shredder voice", value=ss.get("copilot_voice", True),
    help="Trash talk, position-run alarms, villain narration, and your squad's "
         "earned nickname. Turn off for a quiet board.")

starters = {"QB": qb, "RB": rb, "WR": wr, "TE": te, "FLEX": flex, "DST": dst, "K": k}
cfg = E.LeagueConfig(
    teams=int(teams), draft_slot=int(slot), rounds=int(rounds),
    scoring=E.Scoring.preset(scoring_key),
    starters={p: int(v) for p, v in starters.items() if v}, bench=int(bench),
)

# opponents model (used for survival odds + mock bots); rebuilt if size changes
_opp = ss.get("opponents")
if _opp is None or _opp.teams != int(teams) or _opp.my_slot != int(slot):
    ss.opponents = O.LeagueOpponents.default(int(teams), int(slot))
opps = ss.opponents
# Apply any learned manager DNA to the seats EVERY run (rebuilds reset profiles),
# and get the loyalty map so prophecy predicts the actual player each seat covets.
# This is what makes "who's left for me" account for how opponents really draft.
loyalty_by_slot = _apply_dna_to_opponents(opps)

st.sidebar.markdown("---")

# ---- mode-specific setup ----
if mode == "Mock":
    if st.sidebar.button("🎬 Start / restart mock", use_container_width=True,
                         type="primary"):
        _start_mock(cfg, opps)
        st.rerun()
    if ss.mock_on:
        st.sidebar.caption(f"Mock running · overall pick {ss.current_overall} · "
                           f"your roster: {len(ss.my_roster)}")
    # Practice against specific archetypes: hand-tag any seat's tendency so the
    # bots (and the crystal ball) behave like that manager type.
    with st.sidebar.expander("🧬 Set opponent tendencies (practice)"):
        st.caption("Tag any seat so the bots draft like that archetype — great "
                   "for rehearsing against a known leaguemate's style.")
        _TEND = ["ADP-robot", "RB-heavy", "WR-zealot", "zero-RB", "hero-RB",
                 "QB-early", "TE-premium"]
        _tag_slot = st.selectbox("Seat", [s for s in range(1, int(teams) + 1)
                                          if s != int(slot)], key="mock_tag_slot")
        _cur = (opps.profiles[_tag_slot].tendencies
                if _tag_slot in opps.profiles else ["ADP-robot"])
        _tags = st.multiselect("Tendencies", _TEND,
                               default=[t for t in _cur if t in _TEND],
                               key="mock_tag_tags")
        _averse = st.checkbox("Rookie-averse", key="mock_tag_rookie")
        if st.button("Apply to seat", key="mock_tag_apply"):
            prof = opps.profiles.get(int(_tag_slot))
            if prof is not None:
                prof.tendencies = _tags or ["ADP-robot"]
                prof.rookie_averse = bool(_averse)
                ss.setdefault("_manual_tendencies", {})[int(_tag_slot)] = {
                    "tendencies": prof.tendencies, "rookie_averse": prof.rookie_averse}
                st.success(f"Seat {_tag_slot} → {', '.join(prof.tendencies)}")
        if ss.get("_manual_tendencies"):
            st.caption("Tagged seats: " + " · ".join(
                f"{s}:{','.join(v['tendencies'])}"
                for s, v in ss["_manual_tendencies"].items()))

elif mode == "Manual":
    if st.sidebar.button("🔄 Reset board", use_container_width=True):
        _reset_board()
        st.rerun()
    st.sidebar.caption("You enter every pick. Use the search box up top to mark "
                       "players gone; use the board buttons to draft your own.")

elif mode == "ESPN":
    if EC is None:
        st.sidebar.error("ESPN client unavailable (install `requests`).")
    else:
        have_ck = bool(ss.espn_s2 and ss.espn_swid)
        # DESKTOP-only: the ESPN login popup opens a real browser window, which
        # can't happen on a headless server. On the CLOUD everyone pastes their
        # own cookies (below), kept only in their private session.
        if not IS_CLOUD and not have_ck and ELOGIN is not None:
            if st.sidebar.button("🔐 Log in to ESPN", use_container_width=True):
                with st.spinner("Opening ESPN login window…"):
                    r = ELOGIN.login()
                if r.get("espn_s2") and r.get("swid"):
                    ss.espn_s2, ss.espn_swid = r["espn_s2"], r["swid"]
                    if SEC:
                        SEC.save_file(r["espn_s2"], r["swid"])
                    st.rerun()
                else:
                    st.sidebar.error("Login didn't complete.")
        if IS_CLOUD and not have_ck:
            st.sidebar.markdown("**Connect your ESPN account**")
            st.sidebar.caption(
                "Paste your two ESPN cookies below (they stay private to your "
                "session — never saved on the server). To get them: on a computer "
                "logged into fantasy.espn.com, open DevTools (F12) → Application → "
                "Cookies → fantasy.espn.com → copy `espn_s2` and `SWID`.")
        with st.sidebar.expander("ESPN cookies (espn_s2 + SWID)", expanded=not have_ck):
            s2 = st.text_input("espn_s2", type="password", value=ss.espn_s2)
            swid = st.text_input("SWID", type="password", value=ss.espn_swid)
            if st.button("Use these cookies"):
                ss.espn_s2, ss.espn_swid = s2, swid
                # persist to disk ONLY on the desktop (single user). On the cloud
                # keep them session-only so users never share a cookie file.
                if not IS_CLOUD and SEC:
                    SEC.save_file(s2, swid)
                st.rerun()
            if have_ck and st.button("Forget cookies"):
                ss.espn_s2 = ss.espn_swid = ""
                ss["_my_leagues"] = []
                if not IS_CLOUD and SEC:
                    try:
                        SEC.forget()
                    except Exception:  # noqa: BLE001
                        pass
                st.rerun()

        # ---- auto-discover: paste cookies, then pick your league ----
        _saved = _saved_leagues_load()
        if have_ck and st.sidebar.button("🔎 Find my leagues",
                                         use_container_width=True):
            with st.spinner("Asking ESPN for your leagues…"):
                res = EC.discover_leagues(ss.espn_s2, ss.espn_swid)
            if res.get("ok") and res.get("leagues"):
                _saved_leagues_upsert(res["leagues"])
                ss.espn_status = res["message"]
                st.rerun()
            else:
                st.sidebar.error(res.get("message", "Discovery failed."))

        league_id, season = "", 2026
        if _saved:
            _opts = ["— pick a league —"] + [
                (e.get("league_name") or e.get("label") or f"League {e['league_id']}")
                + f" · {e.get('season', 2026)}" for e in _saved]
            _sel = st.sidebar.selectbox("Your leagues", _opts, key="espn_pick")
            if _sel != _opts[0]:
                _e = _saved[_opts.index(_sel) - 1]
                league_id = str(_e["league_id"])
                season = int(_e.get("season", 2026))
                if _e.get("my_team_name"):
                    st.sidebar.caption(f"🏈 Your team: **{_e['my_team_name']}**")
        # manual fallback (or when nothing discovered yet)
        if not str(league_id).strip().isdigit():
            league_id = st.sidebar.text_input("…or ESPN league ID", key="espn_lid")
            season = st.sidebar.number_input("Season", 2020, 2030, 2026)
        ss.dna_seasons = st.sidebar.text_input(
            "🧬 Learn opponent DNA from seasons", value=ss.get("dna_seasons", ""),
            help="Past seasons of THIS league. Shredder learns how each manager "
                 "drafts (RB-heavy, WR-zealot, QB-early, their loyalty picks) and "
                 "uses it to predict who they'll take — so it knows who survives "
                 "to your pick. Blank = skip.")
        if st.sidebar.button("⚡ Connect", type="primary", use_container_width=True):
            if not str(league_id).strip().isdigit():
                ss.espn_status = "Enter a numeric league ID first."
            else:
                try:
                    cli = EC.EspnClient(int(league_id), int(season),
                                        ss.espn_s2, ss.espn_swid)
                    ok, msg = cli.verify()
                    ss.espn = cli if ok else None
                    ss.espn_status = msg
                    if ok:
                        _sync_espn(cfg, force=True)
                        with st.spinner("Learning opponent draft DNA…"):
                            _learn_dna(league_id, season, ss.dna_seasons)
                except Exception as ex:  # noqa: BLE001
                    ss.espn = None
                    ss.espn_status = f"Connect failed: {ex}"
        if ss.espn_status:
            (st.sidebar.success if ss.espn else st.sidebar.error)(ss.espn_status)
        if ss.get("_dna_note"):
            st.sidebar.caption(ss._dna_note)
        # re-learn button (e.g. after the draft order is set)
        if ss.espn and st.sidebar.button("🧬 Re-learn opponent DNA",
                                         use_container_width=True):
            with st.spinner("Learning opponent draft DNA…"):
                _learn_dna(league_id, season, ss.dna_seasons)
            st.rerun()

# undo is always available
st.sidebar.markdown("---")
if st.sidebar.button("↩️ Undo last pick", use_container_width=True):
    if _undo():
        st.toast("Reverted the last pick.")
        st.rerun()


# --------------------------------------------------------------------------- gate
_ready = (mode == "Manual") or (mode == "Mock" and ss.mock_on) \
    or (mode == "ESPN" and ss.espn is not None)
if not _ready:
    if mode == "Mock":
        st.info("Hit **Start / restart mock** in the sidebar to practice against "
                "AI bots that draft between your turns.")
    elif mode == "ESPN":
        st.info("Connect your ESPN league in the sidebar to sync live picks. "
                "No league handy? Switch to **Mock** or **Manual**.")
    st.subheader("How it works")
    st.markdown(
        "- The **hero card** shows the single best pick for *your* roster right now.\n"
        "- Someone else drafts a guy? Type his name in the **search box** and hit "
        "**Gone** — the next-best pick updates instantly.\n"
        "- Draft your own pick with the green **Draft** button; your lineup fills in "
        "on the right.\n"
        "- Suggestions respect your roster: once a starting slot is filled, it stops "
        "pushing that position.")
    st.stop()


# --------------------------------------------------------------------------- board data
# The roster the engine sees = my_roster merged with anything recorded to my slot
# (so ESPN sync / mock picks under my seat are never missed by the need logic).
_mine = list(ss.my_roster)
_seen = {n for n, _ in _mine}
_my_slot_players = ss.team_rosters.get(int(slot), [])
for _nm in _my_slot_players:
    if _nm not in _seen:
        _raw = name_to_raw.get(_nm)
        if _raw:
            _mine.append((_nm, _raw.position)); _seen.add(_nm)
roster = X.Roster(players=_mine)
rstate = X.roster_state(roster, cfg)

recs = X.recommend(pool, cfg, roster, set(ss.drafted),
                   current_overall=int(ss.current_overall),
                   scoring_key=scoring_key, top_n=60, opponents=opps,
                   prefer_floor=prefer_floor)

# whose turn / timing
ov = int(ss.current_overall)
total_picks = int(teams) * int(rounds)
draft_complete = ov > total_picks
on_clock_slot = O._snake_slot(min(ov, total_picks), int(teams))
is_my_turn = (not draft_complete) and (on_clock_slot == int(slot))
my_picks = cfg.my_overall_picks()
next_pick = next((p for p in my_picks if p >= ov and p <= total_picks), None)
picks_until = (next_pick - ov) if next_pick else 0
rnd_now = min((ov - 1) // int(teams) + 1, int(rounds))


# --------------------------------------------------------------------------- header
st.caption(f"🏈 {scoring_label} · {teams}-team · your slot {slot}")

hc = st.columns([1, 1, 1, 1])
hc[0].metric("On the clock", "DONE" if draft_complete else f"#{ov}")
hc[1].metric("Round", f"{rnd_now}/{int(rounds)}")
hc[2].metric("Your next pick",
             "—" if (draft_complete or not next_pick) else f"#{next_pick}",
             "DONE" if draft_complete else
             ("NOW" if is_my_turn else (f"in {picks_until}" if picks_until else "—")))
hc[3].metric("Your players", f"{len(_mine)}/{int(rounds)}")

# top-level view switch: live draft board · full rankings cheat sheet · weekly start/sit
_view = st.radio("View",
                 ["🎯 Draft board", "📋 Rankings guide", "📅 Weekly lineup"],
                 horizontal=True, label_visibility="collapsed", key="top_view")

if mode == "ESPN" and ss.espn:
    sc = st.columns([1, 1, 4])
    if sc[0].button("🔄 Sync now"):
        _sync_espn(cfg, force=True)
        st.rerun()
    auto = sc[1].toggle("Auto-sync", value=False)
    if ss.get("sync_note"):
        sc[2].caption(ss.sync_note)
    if auto:
        _sync_espn(cfg)
        import time as _t
        _t.sleep(4)
        st.rerun()


# =========================================================================== RANKINGS GUIDE
def _render_rankings():
    """A full best-to-worst cheat sheet for the whole player pool. Ranks are
    STABLE (neutral value, not roster-context) so it reads like a reference
    board; drafted players get crossed off dynamically as picks come in."""
    st.markdown("#### 📋 Full rankings guide")
    st.caption("Every player, best to worst, by Shredder's value composite vs "
               "consensus ADP. Drafted players are crossed off automatically. "
               "This is a neutral reference board — the Draft board tab is the one "
               "that factors in YOUR roster needs.")

    try:
        rows = _rankings_cache(scoring_key)
    except Exception as ex:  # noqa: BLE001
        st.error(f"Couldn't build rankings: {ex}")
        return

    drafted = set(ss.drafted)
    mine = {n for n, _ in _mine}
    total = len(rows)
    remaining = sum(1 for r in rows if r.name not in drafted)

    fc = st.columns([2, 2, 2, 2])
    pos_filter = fc[0].multiselect("Position", ["QB", "RB", "WR", "TE", "K", "DST"],
                                   placeholder="All", label_visibility="collapsed")
    hide_drafted = fc[1].toggle("Hide drafted", value=False)
    only_value = fc[2].toggle("Value picks only", value=False,
                              help="Show only players Shredder ranks well ahead "
                                   "of their ADP.")
    fc[3].metric("On the board", f"{remaining}/{total}")

    view = rows
    if pos_filter:
        view = [r for r in view if r.position in pos_filter]
    if only_value:
        view = [r for r in view if r.verdict == "VALUE"]
    if hide_drafted:
        view = [r for r in view if r.name not in drafted]

    st.markdown(
        '<div class="rankhdr">'
        '<span style="width:44px;">#</span>'
        '<span style="width:64px;">POS</span>'
        '<span style="flex:1;">PLAYER</span>'
        '<span style="width:52px;">TIER</span>'
        '<span style="width:70px;">ADP</span>'
        '<span style="width:110px;">VS MARKET</span>'
        '<span style="width:120px;">ACTION</span></div>',
        unsafe_allow_html=True)

    # cap the rendered rows for performance; filters narrow it as needed
    LIMIT = 180
    shown = view[:LIMIT]
    for r in shown:
        gone = r.name in drafted
        is_mine = r.name in mine
        vcls = {"VALUE": "vr-value", "REACH": "vr-reach"}.get(r.verdict, "vr-fair")
        vtxt = (f'{r.verdict} {"+" if (r.delta or 0) >= 0 else ""}{r.delta}'
                if r.delta is not None else "—")
        namestyle = ("text-decoration:line-through;color:var(--dim);opacity:.55;"
                     if gone else "color:var(--txt);")
        mine_tag = ('<span class="mine-tag">YOURS</span>' if is_mine
                    else ('<span class="gone-tag">DRAFTED</span>' if gone else ""))
        rowbg = "background:rgba(51,214,159,.06);" if is_mine else ""
        c = st.columns([0.5, 0.7, 4.2, 0.6, 0.9, 1.3, 1.4])
        c[0].markdown(f'<div class="rk" style="{rowbg}">{r.shredder_rank}</div>',
                      unsafe_allow_html=True)
        c[1].markdown(f'<span class="pill pos-{r.position}">{r.position}{r.pos_rank}</span>',
                      unsafe_allow_html=True)
        c[2].markdown(
            f'<div style="{rowbg}"><span class="pname" style="{namestyle}">{r.name}</span> '
            f'<span class="pmeta">{r.team}</span> {mine_tag}</div>',
            unsafe_allow_html=True)
        c[3].markdown(f'<span class="pmeta">T{r.tier}</span>', unsafe_allow_html=True)
        c[4].markdown(f'<span class="pmeta">{r.consensus_adp if r.consensus_adp else "—"}</span>',
                      unsafe_allow_html=True)
        c[5].markdown(f'<span class="vr {vcls}">{vtxt}</span>', unsafe_allow_html=True)
        if gone:
            if c[6].button("↩ Undo", key=f"rk_undo_{r.name}",
                           use_container_width=True,
                           help="Put this player back on the board"):
                _restore_player(r.name)
                st.rerun()
        else:
            bc = c[6].columns(2)
            if bc[0].button("Mine", key=f"rk_mine_{r.name}",
                            use_container_width=True):
                _record_pick(cfg, r.name, r.position, mine=True, opps=opps)
                st.rerun()
            if bc[1].button("Gone", key=f"rk_gone_{r.name}",
                            use_container_width=True):
                _mark_gone(cfg, r.name, opps)
                st.rerun()
    if len(view) > LIMIT:
        st.caption(f"Showing top {LIMIT} of {len(view)} — filter by position to "
                   "see deeper into the pool.")


def _render_weekly_lineup():
    """Start/sit for the week: the optimal lineup from YOUR roster plus a plain-
    English reason to start or bench each player (matchup, bye, dome, role)."""
    st.markdown("#### 📅 Weekly lineup — who to start")
    st.caption("Builds your best legal starting lineup for a given week and "
               "explains every start/sit call. Uses the players on your team.")

    if not _mine:
        st.info("You haven't drafted anyone yet. Draft your team on the "
                "**🎯 Draft board**, then come here to set your weekly lineup.")
        return

    wk = st.number_input("NFL week", 1, 18, 1)
    try:
        rows = LO.optimize_week(_mine, pool, cfg, int(wk), scoring_key)
    except Exception as ex:  # noqa: BLE001
        st.error(f"Couldn't build the lineup: {ex}")
        return

    starters_rows = [s for s in rows if s.started]
    bench_rows = [s for s in rows if not s.started]
    proj_total = round(sum(s.weekly_points for s in starters_rows), 1)

    mc = st.columns([1, 1, 2])
    mc[0].metric("Projected starters", proj_total)
    mc[1].metric("On bye", sum(1 for s in rows if "ON BYE" in s.narrative))

    def _row(s, dim=False):
        style = "opacity:.55;" if dim else ""
        slotchip = (f'<span class="pill pos-{s.position}">{s.slot}</span>'
                    if s.started else '<span class="gone-tag">BENCH</span>')
        reason = s.narrative.split("): ", 1)[-1]
        st.markdown(
            f'<div class="rowline" style="{style}">{slotchip} '
            f'<span class="pname" style="font-size:14px;">{s.name}</span> '
            f'<span class="pmeta">{s.team} · {s.weekly_points} pts</span><br>'
            f'<span class="pmeta">{reason}</span></div>', unsafe_allow_html=True)

    st.markdown("##### ✅ Start")
    for s in starters_rows:
        _row(s)
    if bench_rows:
        st.markdown("##### 🪑 Bench")
        for s in bench_rows:
            _row(s, dim=True)


if _view == "📋 Rankings guide":
    _render_rankings()
    st.stop()

if _view == "📅 Weekly lineup":
    _render_weekly_lineup()
    st.stop()


# ---- DRAFT COMPLETE: stop the board, show the graded summary ----
if draft_complete:
    _render_draft_summary(cfg, scoring_key)
    st.stop()


# on-the-clock banner
if is_my_turn:
    st.success(f"⏱ **You're on the clock** — overall #{ov}, round {rnd_now}. "
               "Draft your pick below.")
else:
    who = (opps.profiles[on_clock_slot].name
           if on_clock_slot in opps.profiles and opps.profiles[on_clock_slot].name
           else f"Team in slot {on_clock_slot}")
    st.info(f"🕒 On the clock: **{who}** (overall #{ov}). Your next pick is "
            f"#{next_pick} — {picks_until} away. Mark picks as they happen below.")

# DNA readiness pill — so you KNOW whether the snipe/prophecy engine is live
if mode == "ESPN":
    _dna_ready = bool(ss.get("_mgr_dna")) and bool(ss.get("_slot_to_owner"))
    _n_seats = len(ss.get("_slot_to_owner") or {})
    if _dna_ready:
        st.success(f"🧬 Opponent DNA active — {_n_seats} rival seats mapped. "
                   "Snipes and survival odds are reading how each manager drafts.")
    elif ss.get("_mgr_dna"):
        st.warning("🧬 DNA learned but NOT mapped to seats yet — ESPN hasn't "
                   "posted this year's draft order. Hit **🧬 Re-learn opponent "
                   "DNA** in the sidebar once the order is set (usually right "
                   "before the draft) to activate snipes.")

# 🔮 crystal ball: predict rivals' picks + who survives to you (DNA-powered)
_render_rivals_forecast(cfg, opps, loyalty_by_slot)


# --------------------------------------------------------------------------- someone got drafted
st.markdown("#### Someone just got picked?")
sg = st.columns([4, 1, 1])
available_names = [p.name for p in pool if p.name not in ss.drafted]
picked = sg[0].selectbox(
    "Search the player another team drafted", ["—"] + available_names,
    label_visibility="collapsed",
    help="Type to filter. Mark them gone and your next-best pick updates instantly.")
if sg[1].button("✗ Gone (other team)", use_container_width=True,
                disabled=(picked == "—")):
    _mark_gone(cfg, picked, opps)
    st.rerun()
if sg[2].button("✓ I drafted them", use_container_width=True,
                disabled=(picked == "—")):
    raw = name_to_raw.get(picked)
    _record_pick(cfg, picked, raw.position if raw else "?", mine=True, opps=opps)
    st.rerun()

st.divider()


# --------------------------------------------------------------------------- main split
left, right = st.columns([2.3, 1])

with left:
    # ---- hero: the one best pick for MY roster ----
    if recs:
        best = recs[0]
        need_line = _need_summary(rstate)
        st.markdown(
            f'<div class="hero"><span class="tag">⚡ BEST PICK FOR YOUR ROSTER</span>'
            f'<div class="nm">{best.name} '
            f'<span class="pill pos-{best.position}">{best.position}</span> '
            f'<span class="pmeta">{best.team}</span></div>'
            f'<div>{_badges_html(best.badges, 5)}</div>'
            f'<div class="pmeta" style="margin-top:6px;">VORP {best.vorp} · '
            f'tier T{best.tier} · ADP {best.adp if best.adp else "—"}'
            f'{(" · " + str(int(best.survival*100)) + "% survives to your next pick") if best.survival is not None else ""}'
            f'</div>'
            f'<div class="pmeta" style="margin-top:4px;">{need_line}</div></div>',
            unsafe_allow_html=True)
        bcol = st.columns(2)
        if bcol[0].button(f"✓ Draft {best.name.split()[-1]}", type="primary",
                          use_container_width=True, key="hero_draft"):
            _record_pick(cfg, best.name, best.position, mine=True, opps=opps)
            st.rerun()
        if bcol[1].button("✗ Gone", use_container_width=True, key="hero_gone"):
            _mark_gone(cfg, best.name, opps)
            st.rerun()
        _render_why(best, key="hero")
    else:
        st.warning("No players available. Reset the board or check your data.")

    # ---- contextual insights: pop up only when the situation applies ----
    try:
        _insights = DI.gather(pool, cfg, _mine, set(ss.drafted),
                              int(ss.current_overall), recs, opps, scoring_key,
                              name_to_raw, loyalty_by_slot=loyalty_by_slot)
    except Exception:  # noqa: BLE001 — insights must never block the board
        _insights = []
    if _insights:
        st.markdown("#### 💡 Worth a look right now")
        _render_insights(_insights, cfg, opps)

    # ---- best available list ----
    st.markdown("#### Best available")
    fcol = st.columns([3, 1])
    pos_filter = fcol[0].multiselect("Filter by position",
                                     ["QB", "RB", "WR", "TE", "K", "DST"],
                                     label_visibility="collapsed",
                                     placeholder="All positions")
    only_needs = fcol[1].toggle("Needs only", value=False,
                                help="Show only positions that still fill a "
                                     "starting slot.")
    shown = recs[1:] if recs else []
    if pos_filter:
        shown = [r for r in shown if r.position in pos_filter]
    if only_needs:
        open_pos = {p for p in _POS_ORDER
                    if p != "FLEX" and rstate["starter_open"].get(p, 0) > 0}
        if rstate["flex_open"] > 0:
            open_pos |= {"RB", "WR", "TE"}
        shown = [r for r in shown if r.position in open_pos]

    for r in shown[:24]:
        with st.container(border=True):
            # One self-contained row block (renders well on desktop AND phone —
            # no fragile multi-column split that collapses into slivers on mobile).
            vva = ""
            if r.value_vs_adp is not None:
                sign = "+" if r.value_vs_adp >= 0 else ""
                vva = f' · vs ADP {sign}{r.value_vs_adp}'
            surv = (f' · {int(r.survival*100)}% to next'
                    if r.survival is not None else "")
            inj = (f' <span class="bdg bdg-urgent">{r.injury_chip}</span>'
                   if r.injury_chip else "")
            adp_txt = f"ADP {r.adp:.0f}" if r.adp else "ADP —"
            st.markdown(
                f'<span class="pill pos-{r.position}">{r.position}</span> '
                f'<span class="pname">{r.name}</span>{inj}<br>'
                f'<span class="pmeta">{r.team} · VORP {r.vorp} · T{r.tier} · '
                f'{adp_txt}{vva}{surv}</span><br>'
                f'<div style="margin-top:4px;">{_badges_html(r.badges, 3)}</div>',
                unsafe_allow_html=True)
            bc = st.columns(2)
            if bc[0].button("✓ Draft", key=f"d_{r.name}", use_container_width=True):
                _record_pick(cfg, r.name, r.position, mine=True, opps=opps)
                st.rerun()
            if bc[1].button("✗ Gone", key=f"g_{r.name}", use_container_width=True):
                _mark_gone(cfg, r.name, opps)
                st.rerun()
            _render_why(r, key=f"row_{r.name}")

with right:
    _render_roster_panel(rstate, cfg)
    _render_recent_picks()
