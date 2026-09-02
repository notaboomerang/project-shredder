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
    import live_games as LG      # live scoreboard + betting-edge (model vs market)
except Exception:  # noqa: BLE001
    LG = None
try:
    import odds_feed as OF       # multi-book odds + player props (The Odds API)
except Exception:  # noqa: BLE001
    OF = None
try:
    import prob_history as PH    # log model-vs-market probs, settle, calibrate
except Exception:  # noqa: BLE001
    PH = None
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
                   page_icon=(_ICON if _os.path.exists(_ICON) else "▲"),
                   # "auto" = collapsed on phones (board first), expanded on desktop
                   initial_sidebar_state="auto")


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
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=JetBrains+Mono:wght@400;700;800&family=Inter:wght@400;600;700&display=swap');
:root{
  /* pure black to match the Shredder icon's background exactly, so the icon
     blends seamlessly into the page. panels are black too; separation comes
     from thin borders, not fills. */
  --bg:#000000; --panel:#000000; --panel2:#0a0a0a; --line:#242424;
  --txt:#ffffff; --dim:#ffffff; --accent:#ffffff; --accent2:#ffffff;
  --warn:#ffffff; --bad:#ffffff; --good:#ffffff;
  /* one header font used everywhere a title appears, so the app reads as one piece */
  --head:'Space Grotesk','Inter',-apple-system,Segoe UI,Roboto,sans-serif;
  --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
  --body:'Inter',-apple-system,Segoe UI,Roboto,sans-serif;
}
.stApp{background:#000000; font-family:var(--body); color:#ffffff;}
[data-testid="stAppViewContainer"],[data-testid="stHeader"],
section[data-testid="stSidebar"]{background:#000000 !important;}
/* ALL TEXT WHITE by default */
.stApp, .stApp p, .stApp span, .stApp div, .stApp li, .stApp label,
[data-testid="stMarkdownContainer"]{color:#ffffff;}
/* MATCH THE HEADER FONT THROUGHOUT: every Streamlit heading + our section titles
   render in the same display face as the masthead, so headers feel unified. */
.stApp h1,.stApp h2,.stApp h3,.stApp h4,
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3,
[data-testid="stMarkdownContainer"] h4{
  font-family:var(--head)!important;font-weight:700!important;
  letter-spacing:.2px;color:#ffffff !important;}
/* LINKS: white, underlined; invert to black-on-white on hover */
.stApp a, [data-testid="stMarkdownContainer"] a{
  color:#ffffff !important;text-decoration:underline !important;
  text-underline-offset:2px;transition:background .12s,color .12s;}
.stApp a:hover, [data-testid="stMarkdownContainer"] a:hover{
  color:#000000 !important;background:#ffffff !important;
  text-decoration:none !important;}
[data-testid="stMetricValue"]{font-size:22px;font-weight:800;color:var(--txt);}
[data-testid="stMetricLabel"]{color:var(--dim);text-transform:uppercase;
  font-size:11px;letter-spacing:.5px;}
[data-testid="stVerticalBlockBorderWrapper"]{background:var(--panel);
  border:1px solid var(--line)!important;border-radius:10px;}
.stButton>button{border-radius:8px;font-weight:700;border:1px solid var(--line);
  background:var(--panel2);color:var(--txt);}
.stButton>button:hover{border-color:var(--accent);color:var(--accent);}
.stButton>button[kind="primary"]{background:#ffffff;color:#000000 !important;
  border:1px solid #ffffff;}
.stButton>button[kind="primary"] *{color:#000000 !important;}
.stButton>button[kind="primary"]:hover{background:#d9d9d9;color:#000000 !important;}
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
.hero{background:#000000;border:2px solid #ffffff;
  border-radius:14px;padding:18px 22px;margin-bottom:14px;
  box-shadow:0 0 24px rgba(255,255,255,.08);}
.hero .tag{font:800 12px var(--mono);color:var(--accent);
  letter-spacing:1.5px;}
.hero .nm{font-family:var(--head);font-size:30px;font-weight:700;color:var(--txt);margin:2px 0;}
.masthead{display:flex;align-items:center;gap:16px;
  background:#000000;border:none;border-radius:0;
  padding:6px 2px 10px;margin-bottom:12px;box-shadow:none;
  border-bottom:1px solid var(--line);}
/* bigger icon so the knuckle tattoos are readable; no border/radius so its own
   black background is invisible against the page — the fists just float. */
.masthead img{width:76px;height:76px;border-radius:0;border:none;
  background:#000000;}
.mono-mark{display:inline-flex;align-items:center;justify-content:center;
  width:76px;height:76px;border-radius:12px;flex:0 0 76px;
  font-family:var(--head);font-weight:700;font-size:26px;letter-spacing:1px;
  color:#ffffff;background:#000000;border:2px solid #ffffff;}
.masthead h1{font-family:var(--head);font-size:27px;font-weight:700;margin:0;
  letter-spacing:.4px;color:var(--txt);}
.masthead h1 .lo{color:var(--accent);}
.masthead .sub{color:var(--dim);font-size:12px;margin-top:3px;
  font-family:var(--mono);letter-spacing:.3px;}
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
.insight .it{font-family:var(--head);font-weight:700;font-size:13.5px;color:var(--txt);}
.insight .ib{font-size:12.5px;color:var(--dim);margin-top:2px;line-height:1.45;}

/* ===================== SIDEBAR — a touch smaller + tidy ================== */
section[data-testid="stSidebar"]{font-size:13px;}
section[data-testid="stSidebar"] .stMarkdown p,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] .stCaption,
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p{
  font-size:12.5px !important;}
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3{
  font-family:var(--head)!important;font-size:15px !important;
  letter-spacing:.3px;margin-bottom:.2rem;}
/* the app wordmark at the very top of the sidebar */
.sb-brand{font-family:var(--head);font-weight:700;font-size:17px;letter-spacing:.4px;
  color:var(--txt);margin:0 0 2px;}
.sb-brand .lo{color:var(--accent);}
section[data-testid="stSidebar"] .stButton>button{font-size:12.5px;}
section[data-testid="stSidebar"] input,
section[data-testid="stSidebar"] [data-baseweb="select"]{font-size:12.5px !important;}

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
  /* masthead: smaller so it doesn't eat the first screen, but keep the icon
     big enough to read the knuckle tattoos */
  .masthead{padding:6px 2px 8px !important; gap:12px !important;}
  .masthead h1{font-size:21px !important;}
  .masthead .sub{font-size:10px !important;}
  .masthead img{width:58px !important; height:58px !important;}
  .mono-mark{width:58px !important;height:58px !important;flex:0 0 58px !important;
    font-size:20px !important;}
  /* tables/rows: tighter, wrap-friendly */
  .pname{font-size:15px !important;}
  .pmeta{font-size:12px !important;}
  .bdg{font-size:10px !important;}
  /* sidebar is a drawer on mobile; widen its controls */
  section[data-testid="stSidebar"]{min-width:88vw !important;}
  /* highlight the native open-sidebar control so it's easy to find/tap — but
     DON'T transform/scale it (that can clip or misposition the tap target and
     block reaching Setup on some Streamlit builds). Just tint + enlarge hit area. */
  [data-testid="stSidebarCollapsedControl"]{
    background:var(--accent) !important; border-radius:8px !important;
    padding:2px !important;}
  [data-testid="stSidebarCollapsedControl"] svg,
  [data-testid="stSidebarCollapsedControl"] button svg{color:#06231a !important;}
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
    ss.setdefault("alerts_on", True)         # sound + vibrate when you're on the clock
    ss.setdefault("intent", "")              # "" until the first-run gate is answered
    ss.setdefault("peek_mode", False)        # read-only phone mirror of a live draft
    ss.setdefault("_peek_league", "")        # league id carried in the peek link
    ss.setdefault("_peek_season", 0)         # season carried in the peek link
    ss.setdefault("_peek_slot", 0)           # your draft slot carried in the peek link
    ss.setdefault("_peek_teams", 0)          # league size carried in the peek link
    ss.setdefault("_peek_rounds", 0)         # rounds carried in the peek link
    ss.setdefault("_peek_scoring", "")       # scoring key carried in the peek link

    # ONE-TAP LOGIN via bookmarklet: if the app was opened with cookies in the
    # URL (?espn_s2=...&swid=...), pull them into the session and immediately
    # scrub them from the address bar so they don't linger in history/screenshots.
    # This is how leaguemates connect on their phone with a single tap — no typing.
    #
    # PEEK LINK: the desktop "Open on my phone" link adds ?peek=1&league=&season=
    # &slot= alongside the cookies. When present we flip into read-only peek mode
    # and remember the league so we can auto-connect and just mirror the picks.
    try:
        _qp = st.query_params
        _qs2 = _qp.get("espn_s2", "")
        _qsw = _qp.get("swid", "")
        _qpeek = _qp.get("peek", "")
        _qleague = _qp.get("league", "")
        _qseason = _qp.get("season", "")
        _qslot = _qp.get("slot", "")
        _qteams = _qp.get("teams", "")
        _qrounds = _qp.get("rounds", "")
        _qscoring = _qp.get("scoring", "")
        if _qs2 or _qsw or _qpeek:
            import urllib.parse as _up
            if _qs2:
                ss.espn_s2 = _up.unquote(_qs2)
            if _qsw:
                ss.espn_swid = _up.unquote(_qsw)
            if _qs2 or _qsw:
                ss["_cookie_source"] = "peek" if _qpeek else "bookmarklet"
            ss["_cookie_loaded"] = True
            if _qpeek in ("1", "true", "yes"):
                ss.peek_mode = True
                ss.mode = "ESPN"
                if str(_qleague).strip().isdigit():
                    ss["_peek_league"] = str(_qleague).strip()
                if str(_qseason).strip().isdigit():
                    ss["_peek_season"] = int(_qseason)
                if str(_qslot).strip().isdigit():
                    ss["_peek_slot"] = int(_qslot)
                if str(_qteams).strip().isdigit():
                    ss["_peek_teams"] = int(_qteams)
                if str(_qrounds).strip().isdigit():
                    ss["_peek_rounds"] = int(_qrounds)
                if _qscoring:
                    ss["_peek_scoring"] = _up.unquote(_qscoring)
            # remove the secrets (and peek params) from the URL right away
            try:
                for _k in ("espn_s2", "swid", "peek", "league", "season", "slot",
                           "teams", "rounds", "scoring"):
                    if _k in _qp:
                        del _qp[_k]
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass

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
    st.markdown(
        '<div style="font-family:var(--head);font-weight:700;font-size:30px;'
        'letter-spacing:.4px;margin:.2rem 0;">Project '
        '<span style="color:var(--accent);">Shredder</span></div>',
        unsafe_allow_html=True)
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


def _intent_gate():
    """First-run 'what are you here to do?' screen. ONE clear fork so people
    aren't staring at every path at once:
      • Draft on this device  -> the normal setup (ESPN / Mock / Manual).
      • Just watch (peek)      -> scan the QR from the computer running the draft.
    Only shows on the hosted app, only until answered, and NEVER for a peek link
    (those carry ?peek=1 and flow straight to the read-only board). Desktop skips
    it entirely so nothing changes there."""
    if not IS_CLOUD or ss.get("peek_mode") or ss.get("intent"):
        return
    # already connected / mid-draft in this session? skip the gate.
    if ss.get("espn") is not None or ss.get("mock_on"):
        ss.intent = "draft"
        return

    _render_masthead()
    st.markdown("### Welcome — how are you using Shredder right now?")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            "#### 🎯 I'm drafting on this device\n"
            "Connect your ESPN league (or run a mock) and get the best pick, live, "
            "as you draft right here.")
        if st.button("Draft here", type="primary", use_container_width=True,
                     key="intent_draft"):
            ss.intent = "draft"
            st.rerun()
    with c2:
        st.markdown(
            "#### 👀 I'm just watching (peek)\n"
            "Drafting somewhere else and want Shredder's picks on this screen too? "
            "Open the peek link from the computer running Shredder — no login here.")
        if st.button("I'm just watching", use_container_width=True,
                     key="intent_watch"):
            ss.intent = "watch"
            st.rerun()
    with c3:
        st.markdown(
            "#### 📈 Betting edge\n"
            "Live NFL win-prob vs the market, multi-book line shopping, and player "
            "props. No draft or login needed — jump straight in.")
        if st.button("Betting edge", use_container_width=True,
                     key="intent_betting"):
            ss.intent = "betting"
            st.rerun()
    st.caption("You can switch anytime from the sidebar.")
    st.stop()


def _render_watch_help():
    """The 'just watching' landing: dead-simple instructions to get the peek link
    from the device that's running the draft. No cookies, no typing here."""
    _render_masthead()
    st.markdown("### 👀 Watch a draft (peek mode)")
    st.markdown(
        "Peek mode mirrors a draft that's running on **another device** — you just "
        "watch Shredder's picks here, read-only.\n\n"
        "**To start watching:**\n"
        "1. On the computer running Shredder, connect your ESPN league.\n"
        "2. Open **📱 Open on my phone** there — it shows a QR code.\n"
        "3. **Scan that QR with this device's camera.** You'll land right back here, "
        "already connected and auto-updating.\n\n"
        "That's it — nothing to log into on this screen.")
    st.info("No QR yet? Whoever set up the draft needs to hit Connect first, then "
            "the 'Open on my phone' code appears.")
    if st.button("← Actually, I want to draft here", key="watch_back"):
        ss.intent = "draft"
        st.rerun()
    st.stop()


@st.cache_data(show_spinner="Loading player projections…")
def _load_pool():
    # Always prefer the FULL live board; capture the source so we can warn if we
    # silently fell back to the tiny bundled seed (never draft blind on ~34 players).
    return P.load_players_with_source(prefer_live=True)


pool, _pool_source = _load_pool()
name_to_raw = {p.name: p for p in pool}

# Loud, unmissable warning if we're NOT on the full live board — so a real draft
# is never silently run on the small seed fallback. A "reload" clears the cache.
if _pool_source != "live":
    _n = len(pool)
    _msg = ("⚠️ **Running on the bundled backup player list** "
            f"({_n} players), not the full live board — the live projection feed "
            "couldn't be reached. Rankings will be limited. Check your connection "
            "and reload."
            if _pool_source == "seed" else
            "⚠️ **No player data loaded.** The live feed failed and no backup list "
            "was found. Reload to retry.")
    _wc = st.columns([5, 1])
    _wc[0].warning(_msg)
    if _wc[1].button("🔄 Reload data"):
        _load_pool.clear()
        st.rerun()


def _render_masthead():
    """PROJECT SHREDDER masthead — the icon + name + tagline, rendered once up top."""
    # Use the full-res icon so the knuckle tattoos stay crisp at the larger size.
    uri = _icon_data_uri("shredder_icon.png") or _icon_data_uri("shredder_icon_128.png")
    # Clean CSS monogram when there's no icon file — no emoji.
    img = (f'<img src="{uri}" alt="Project Shredder">' if uri else
           '<span class="mono-mark">PS</span>')
    st.markdown(
        f'<div class="masthead">{img}<div>'
        f'<h1>Project <span class="lo">Shredder</span></h1>'
        f'<div class="sub">live-draft copilot · VORP + edge engine · '
        f'contextual insights</div>'
        f'</div></div>', unsafe_allow_html=True)
    # PHONE-ONLY: an obvious 'Setup / Connect' bar that opens the sidebar drawer
    # (setup + ESPN connect live there). Clicks Streamlit's own sidebar toggle so
    # it works reliably; hidden on desktop where the sidebar is already open.
    st.markdown(
        """
<button class="only-mobile shredder-setup-btn" onclick="
  try{
    var d=window.parent&amp;&amp;window.parent.document?window.parent.document:document;
    var sels=['[data-testid=\\'stSidebarCollapsedControl\\'] button',
              '[data-testid=\\'stSidebarCollapsedControl\\']',
              '[data-testid=\\'stExpandSidebarButton\\']',
              '[aria-label=\\'Open sidebar\\']',
              '[data-testid=\\'collapsedControl\\'] button',
              '[data-testid=\\'baseButton-headerNoPadding\\']'];
    var t=null;
    for(var i=0;i&lt;sels.length;i++){t=d.querySelector(sels[i]);if(t){break;}}
    if(t){t.click();}
    else{alert('Tap the small arrow at the very top-left to open Setup.');}
  }catch(e){}
">☰  Setup / Connect ESPN</button>
<style>
.shredder-setup-btn{display:none;}
@media (max-width:820px){
  .shredder-setup-btn{display:block;width:100%;margin:0 0 10px;padding:12px;
    background:var(--accent);color:#06231a;border:none;border-radius:10px;
    font-family:var(--head);font-weight:700;font-size:15px;letter-spacing:.3px;}
}
</style>
""", unsafe_allow_html=True)


# First-run fork (cloud only): drafting here vs. just watching. This renders its
# own masthead and stops the script until answered; peek links skip it entirely.
_intent_gate()
if ss.get("intent") == "watch" and not ss.get("peek_mode"):
    _render_watch_help()

_render_masthead()

# Betting-edge shortcut (chosen from the welcome fork): skip ALL the ESPN/mock/
# draft setup below and render only the betting hub. The render functions are
# defined much further down, so we can't call them here — instead we set a flag,
# render the back button now, and let the guarded blocks below skip straight to
# the betting render that lives right after those functions are defined.
_BETTING_ONLY = (ss.get("intent") == "betting" and not ss.get("peek_mode"))
if _BETTING_ONLY:
    if st.button("← Back", key="betting_back"):
        ss.intent = None
        st.rerun()


def _render_mobile_mode_picker():
    """Phone-only: pick Mock / Manual / ESPN right on the main page, so the mode
    is ALWAYS reachable even if the sidebar drawer is hard to open on mobile.
    Writes ss.mode; the sidebar radio reads the same value. Hidden on desktop."""
    st.markdown('<div class="only-mobile">', unsafe_allow_html=True)
    _cur = st.session_state.get("mode", "Mock")
    _opts = ["Mock", "Manual", "ESPN"]
    pick = st.radio("How are you drafting?", _opts,
                    index=_opts.index(_cur if _cur in _opts else "Mock"),
                    horizontal=True, key="mobile_mode_radio")
    if pick != _cur:
        st.session_state["mode"] = pick
        # keep the sidebar widget in sync so they never disagree
        st.session_state["mode_radio"] = pick
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


if not st.session_state.get("peek_mode"):
    _render_mobile_mode_picker()
else:
    # Peek mode is a clean, read-only mirror — hide the sidebar entirely so the
    # phone is just the board. Nothing to configure here.
    st.markdown("<style>[data-testid='stSidebar']{display:none !important;}"
                "[data-testid='collapsedControl']{display:none !important;}</style>",
                unsafe_allow_html=True)


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


def _is_live_runtime():
    """True only under a real Streamlit server (not the AppTest harness). Used to
    gate the peek auto-refresh loop so tests don't spin forever."""
    try:
        from streamlit import runtime as _rt
        return _rt.exists()
    except Exception:  # noqa: BLE001
        return False


def _picks_until_my_turn(cfg):
    """How many picks until it's MY slot on the clock (0 = right now, 1 = next).
    Uses the app's single source of truth: current_overall + snake-slot mapping.
    Returns None past the end of the draft."""
    try:
        teams = int(cfg.teams)
        my_slot = int(cfg.draft_slot)
        cur = int(ss.get("current_overall", 1))
        last = teams * int(cfg.rounds)
    except Exception:  # noqa: BLE001
        return None
    for k in range(0, teams + 1):          # at most one full round away
        ov = cur + k
        if ov > last:
            return None
        if O._snake_slot(ov, teams) == my_slot:
            return k
    return None


def _is_my_turn(cfg):
    """True when MY slot owns the pick currently on the clock."""
    return _picks_until_my_turn(cfg) == 0


def _render_on_the_clock_alert(cfg):
    """The headline copilot feature: the moment it's YOUR pick, throw a big
    banner, start a count-up timer, play a chime, and buzz the phone. When you're
    1-2 picks away it shows a calmer 'get ready' heads-up instead. Fires the
    sound/vibrate ONCE per turn (guarded in-browser keyed on the overall pick #),
    and respects the alerts on/off toggle. Works the same on the main draft view
    and the read-only phone peek - that's the payoff for glancing at your phone."""
    k = _picks_until_my_turn(cfg)
    if k is None:
        return
    cur = int(ss.get("current_overall", 1))
    alerts_on = bool(ss.get("alerts_on", True))

    if k == 0:
        if ss.get("_clock_turn_overall") != cur:
            ss["_clock_turn_overall"] = cur
        _render_clock_component(cur, alerts_on)
    elif k <= 2:
        who = "1 pick" if k == 1 else f"{k} picks"
        st.markdown(
            f'<div style="background:linear-gradient(100deg,#1a2233,#12261f);'
            f'border:1px solid #2f5aa0;border-radius:12px;padding:10px 14px;'
            f'margin:6px 0;color:#cfe0ff;font-weight:700;">&#9203; Get ready - '
            f"you're <b>{who}</b> away. Line up your pick.</div>",
            unsafe_allow_html=True)


def _render_clock_component(overall, alerts_on):
    """In-browser banner + count-up timer + one-shot chime + vibrate. The chime
    only plays once per overall pick number (stored in sessionStorage) so silent
    auto-refreshes don't re-trigger it."""
    import streamlit.components.v1 as _components
    play = "true" if alerts_on else "false"
    _components.html(f"""
<div id="otc" style="background:linear-gradient(100deg,#3a1020,#10261f);
   border:2px solid #ff5470;border-radius:14px;padding:16px 18px;margin:4px 0;
   box-shadow:0 0 34px rgba(255,84,112,.35);font:-apple-system,Segoe UI,Roboto,sans-serif;">
  <div style="font:800 13px 'JetBrains Mono',monospace;letter-spacing:2px;color:#ff8fa3;">
     &#128293; YOU'RE ON THE CLOCK</div>
  <div style="font-size:26px;font-weight:800;color:#fff;margin-top:2px;">
     Make your pick</div>
  <div style="margin-top:4px;color:#ffd6de;font-weight:700;">
     on the clock for <span id="otc_t">0:00</span></div>
</div>
<script>
(function(){{
  var OV = "{overall}", PLAY = {play};
  var key = 'otc_start_'+OV;
  var start = parseInt(sessionStorage.getItem(key)||'0',10);
  if(!start){{ start = Date.now(); sessionStorage.setItem(key, String(start)); }}
  function tick(){{
    var s = Math.floor((Date.now()-start)/1000);
    var m = Math.floor(s/60), r = s%60;
    var el = document.getElementById('otc_t');
    if(el) el.textContent = m+':'+(r<10?'0':'')+r;
  }}
  tick(); setInterval(tick, 1000);
  var akey = 'otc_alerted_'+OV;
  if(PLAY && !sessionStorage.getItem(akey)){{
    sessionStorage.setItem(akey,'1');
    try{{ if(navigator.vibrate) navigator.vibrate([220,90,220,90,320]); }}catch(e){{}}
    try{{
      var Ctx = window.AudioContext||window.webkitAudioContext;
      if(Ctx){{
        var ac=new Ctx();
        [880,1320,1760].forEach(function(f,i){{
          var o=ac.createOscillator(), g=ac.createGain();
          o.type='sine'; o.frequency.value=f;
          var t0=ac.currentTime+i*0.16;
          g.gain.setValueAtTime(0.0001,t0);
          g.gain.exponentialRampToValueAtTime(0.28,t0+0.03);
          g.gain.exponentialRampToValueAtTime(0.0001,t0+0.30);
          o.connect(g); g.connect(ac.destination);
          o.start(t0); o.stop(t0+0.33);
        }});
      }}
    }}catch(e){{}}
  }}
}})();
</script>
""", height=140)


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
        if player and player not in ss.drafted and not ss.get("peek_mode"):
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


def _app_base_url():
    """Best-effort public base URL of THIS app, for building the bookmarklet.
    Prefers an explicit APP_URL secret; else a sensible default."""
    try:
        if hasattr(st, "secrets"):
            u = st.secrets.get("app_url", "")
            if u:
                return u.rstrip("/")
    except Exception:  # noqa: BLE001
        pass
    return "https://shreddies.streamlit.app"


def _bookmarklet_js(base_url):
    """A one-line bookmarklet: run it while on fantasy.espn.com and it reads
    ESPN's own cookies (same-origin, allowed) and opens Shredder with them in
    the URL so the app auto-connects. No typing, nothing to install."""
    # reads document.cookie for espn_s2 + SWID, URL-encodes, opens the app.
    return (
        "javascript:(function(){"
        "var c=document.cookie,m={};c.split(';').forEach(function(p){"
        "var i=p.indexOf('=');if(i>0){m[p.slice(0,i).trim()]=p.slice(i+1).trim();}});"
        "var s2=m['espn_s2'],sw=m['SWID']||m['swid'];"
        "if(!s2||!sw){alert('Not logged into ESPN here. Open fantasy.espn.com, "
        "log in, then tap this again.');return;}"
        f"var u='{base_url}/?espn_s2='+encodeURIComponent(s2)+'&swid='+encodeURIComponent(sw);"
        "window.open(u,'_blank');"
        "})();"
    )


def _render_bookmarklet_setup():
    """Phone-first one-tap login setup. Auto-detects iPhone / Android / computer
    and shows ONLY the steps for that device, so a leaguemate isn't wading through
    a wall of instructions for platforms they aren't on. One-time ~30s setup,
    then it's a single tap every draft. Uses components.html so the copy button
    and platform switch actually run JS."""
    # Rendered inside an 'Advanced' expander now, so no inner expander.
    _render_bookmarklet_body(st.sidebar, use_expander=False)


def _render_bookmarklet_setup_main():
    """Same one-tap setup, rendered on the MAIN page (for the phone connect panel).
    Rendered inside an 'Advanced' expander now, so no inner expander."""
    _render_bookmarklet_body(st, use_expander=False)


def _peek_url(cfg):
    """Build the 'open on my phone' peek link for the CURRENTLY connected league.
    Carries the read-only cookies + league shape so the phone auto-connects and
    mirrors this exact draft. Returns '' if we're not connected."""
    if not ss.get("espn"):
        return ""
    import urllib.parse as _up
    base = _app_base_url()
    try:
        _lid = int(getattr(ss.espn, "league_id", 0))
        _seas = int(getattr(ss.espn, "season", 2026))
    except Exception:  # noqa: BLE001
        return ""
    params = {
        "peek": "1",
        "espn_s2": ss.espn_s2 or "",
        "swid": ss.espn_swid or "",
        "league": str(_lid),
        "season": str(_seas),
        "slot": str(int(cfg.draft_slot)),
        "teams": str(int(cfg.teams)),
        "rounds": str(int(cfg.rounds)),
        "scoring": ss.get("_scoring_key_for_peek", "") or "",
    }
    return base + "/?" + _up.urlencode(params)


def _render_peek_share(cfg, container):
    """Render the 'Open on my phone' QR + link for the connected league. The QR is
    generated in-browser (tiny JS lib) so we add no Python dependency and it works
    on the cloud. Scanning it opens Shredder on the phone already connected, in
    read-only peek mode, auto-refreshing the live picks."""
    url = _peek_url(cfg)
    if not url:
        return
    import json as _json
    import streamlit.components.v1 as _components
    url_js = _json.dumps(url)
    with container.expander("📱 Open on my phone (read-only peek)", expanded=False):
        st.caption("Scan this with your phone camera. Shredder opens on the phone "
                   "already connected to THIS draft — read-only, auto-refreshing. "
                   "Draft on your computer; glance at the phone for the pick.")
        _components.html(f"""
<div style="font:13px -apple-system,Segoe UI,Roboto,sans-serif;color:#e6e6e6;text-align:center;">
  <div id="qr" style="display:inline-block;background:#fff;padding:10px;border-radius:10px;"></div>
  <div style="margin-top:10px;">
    <button id="cp" style="padding:9px 14px;border:none;border-radius:8px;
      background:#31c48d;color:#06231a;font-weight:800;cursor:pointer;">Copy link</button>
    <span id="ok" style="display:none;color:#31c48d;font-weight:700;margin-left:8px;">✓ copied</span>
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/qrcodejs@1.0.0/qrcode.min.js"></script>
<script>
  var URL_={url_js};
  function draw(){{
    try{{ new QRCode(document.getElementById('qr'),
        {{text:URL_, width:200, height:200, correctLevel:QRCode.CorrectLevel.M}}); }}
    catch(e){{ document.getElementById('qr').textContent='(QR failed — use Copy link)'; }}
  }}
  if(window.QRCode){{ draw(); }} else {{
    var s=document.querySelector('script[src*="qrcode"]');
    if(s){{ s.addEventListener('load',draw); }} else {{ setTimeout(draw,600); }}
  }}
  document.getElementById('cp').addEventListener('click',function(){{
    function ok(){{ document.getElementById('ok').style.display='inline'; }}
    if(navigator.clipboard&&navigator.clipboard.writeText){{
      navigator.clipboard.writeText(URL_).then(ok,function(){{alert(URL_);}});
    }} else {{ alert(URL_); }}
  }});
</script>
""", height=320)
        st.text_input("…or copy the link", value=url, key="peek_link_box")


def _render_bookmarklet_body(container, use_expander=True):
    """Shared body for the one-tap login setup; `container` is st or st.sidebar.
    When `use_expander` is False the caller has already opened a disclosure
    (Streamlit forbids nested expanders), so we render inline instead."""
    import json as _json
    import contextlib
    import streamlit.components.v1 as _components
    base = _app_base_url()
    bm = _bookmarklet_js(base)
    bm_js = _json.dumps(bm)          # safe-embed the bookmarklet as a JS string
    accent = "#31c48d"
    _ctx = (container.expander("📲 One-tap login — set up once (~30s)", expanded=False)
            if use_expander else contextlib.nullcontext())
    with _ctx:
        st.caption("The easiest way to connect. Do this once; after that it's a "
                   "single tap every draft. Your cookies go straight from ESPN "
                   "into your own private session — never stored on the server.")
        _components.html(f"""
<div id="wrap" style="font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#e6e6e6;">
  <button id="copybtn" style="width:100%;padding:12px;border:none;border-radius:10px;
     background:{accent};color:#06231a;font-weight:800;font-size:15px;cursor:pointer;">
     1️⃣  Copy my Shredder login</button>
  <div id="copied" style="display:none;margin:6px 0 0;color:{accent};font-weight:700;">
     ✓ Copied — now do step 2 below.</div>

  <div id="ios" style="display:none;margin-top:14px;">
    <b>iPhone (Safari) — one time:</b>
    <ol style="margin:6px 0 0 18px;padding:0;">
      <li>Tap <b>Share</b> → <b>Add Bookmark</b> → Save.</li>
      <li>Tap the <b>book</b> icon → <b>Edit</b> → open that bookmark.</li>
      <li>Clear the address line, <b>paste</b>, name it <b>Shredder</b> → Done.</li>
    </ol>
  </div>
  <div id="android" style="display:none;margin-top:14px;">
    <b>Android (Chrome) — one time:</b>
    <ol style="margin:6px 0 0 18px;padding:0;">
      <li>Tap <b>⋮</b> → the <b>★</b> to bookmark this page.</li>
      <li>Tap <b>⋮ → Bookmarks</b>, open it → <b>Edit</b> (pencil).</li>
      <li>Replace the URL with the <b>pasted</b> code, name it <b>Shredder</b> → save.</li>
    </ol>
  </div>
  <div id="desktop" style="display:none;margin-top:14px;">
    <b>Computer — one time:</b> drag this button to your bookmarks bar →
    <a id="dtlink" href="#" style="display:inline-block;padding:5px 10px;border:1px solid {accent};
       border-radius:8px;color:{accent};text-decoration:none;font-weight:700;">🔗 Shredder login</a>
  </div>

  <div style="margin-top:14px;padding-top:10px;border-top:1px solid #2a2a2a;">
    <b>Every draft (the easy part):</b>
    <ol style="margin:6px 0 0 18px;padding:0;">
      <li>Open <b>fantasy.espn.com</b>, make sure you're logged in.</li>
      <li>Tap your <b>Shredder</b> bookmark.</li>
      <li>You land back here <b>already connected</b>.</li>
    </ol>
  </div>
</div>
<script>
  var BM = {bm_js};
  var ua = navigator.userAgent || "";
  var isIOS = /iPhone|iPad|iPod/i.test(ua);
  var isAnd = /Android/i.test(ua);
  document.getElementById(isIOS?'ios':(isAnd?'android':'desktop')).style.display='block';
  var dl = document.getElementById('dtlink'); if(dl) dl.setAttribute('href', BM);
  var btn = document.getElementById('copybtn');
  btn.addEventListener('click', function(){{
    function ok(){{ document.getElementById('copied').style.display='block'; }}
    try {{
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(BM).then(ok, function(){{ fallback(); }});
      }} else {{ fallback(); }}
    }} catch(e) {{ fallback(); }}
    function fallback(){{
      var t=document.createElement('textarea'); t.value=BM;
      document.body.appendChild(t); t.select();
      try{{ document.execCommand('copy'); ok(); }}catch(e){{ alert('Copy this:\\n\\n'+BM); }}
      document.body.removeChild(t);
    }}
  }});
</script>
""", height=430)


def _grade_team(players, cfg, scoring_key):
    """Grade one drafted team on (a) total projected value and (b) roster
    construction, and collect plain-English reasons for the grade. `players` =
    list of (name, position). Returns dict:
      proj, con (construction 0-100), counts, top3 (best players by proj),
      pos_reasons (list of 'why' fragments: wins + misses)."""
    import engine as E
    counts = {}
    total_proj = 0.0
    by_proj = []
    for nm, pos in players:
        counts[pos] = counts.get(pos, 0) + 1
        raw = name_to_raw.get(nm)
        pp = E.project_points(raw.stats, cfg.scoring) if raw else 0.0
        total_proj += pp
        by_proj.append((nm, pos, pp))
    by_proj.sort(key=lambda x: x[2], reverse=True)
    s = cfg.starters

    con = 100.0
    wins, misses = [], []
    need = {"QB": s.get("QB", 1), "RB": s.get("RB", 2), "WR": s.get("WR", 2),
            "TE": s.get("TE", 1), "DST": s.get("DST", 1), "K": s.get("K", 1)}
    gaps = []
    for p, want in need.items():
        have = counts.get(p, 0)
        if have < want:
            con -= 18 * (want - have)
            gaps.append(f"{want - have} {p}")
    if gaps:
        misses.append("short at " + ", ".join(gaps))
    else:
        wins.append("all starters filled")

    flex_need = s.get("RB", 2) + s.get("WR", 2) + s.get("TE", 1) + s.get("FLEX", 1)
    skill = counts.get("RB", 0) + counts.get("WR", 0) + counts.get("TE", 0)
    if skill < flex_need:
        con -= 12
        misses.append("thin FLEX/skill depth")
    elif skill >= flex_need + 3:
        wins.append("deep skill-position bench")

    hoarded = []
    for p in ("QB", "TE", "DST", "K"):
        over = counts.get(p, 0) - max(1, s.get(p, 1)) - 1
        if over > 0:
            con -= 6 * over
            hoarded.append(f"{counts.get(p,0)} {p}")
    if hoarded:
        misses.append("over-invested: " + ", ".join(hoarded))
    con = max(0.0, min(100.0, con))

    return {"proj": total_proj, "con": con, "counts": counts,
            "top3": by_proj[:3], "wins": wins, "misses": misses}


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
        g = _grade_team(players, cfg, scoring_key)
        graded.append({"slot": slot, "players": players, "proj": g["proj"],
                       "con": g["con"], "counts": g["counts"],
                       "top3": g.get("top3", []), "wins": g.get("wins", []),
                       "misses": g.get("misses", [])})

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
            # why this grade: best players + the wins/misses that moved the needle
            top3 = " · ".join(f"{nm}" for nm, _p, _pp in g.get("top3", [])[:3])
            why_bits = []
            for w in g.get("wins", [])[:2]:
                why_bits.append(f'<span style="color:var(--good);">✓ {w}</span>')
            for m in g.get("misses", [])[:2]:
                why_bits.append(f'<span style="color:var(--warn);">✗ {m}</span>')
            why = " · ".join(why_bits)
            c[1].markdown(
                f'<div><b>#{rank} · {who}</b>{tag}</div>'
                f'<div class="pmeta">{mix}</div>'
                + (f'<div class="pmeta">best: {top3}</div>' if top3 else "")
                + (f'<div class="pmeta">{why}</div>' if why else ""),
                unsafe_allow_html=True)
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
st.sidebar.markdown(
    '<div class="sb-brand">Project <span class="lo">Shredder</span></div>',
    unsafe_allow_html=True)

_MODES = ["Mock", "Manual", "ESPN"]
mode = st.sidebar.radio("How are you drafting?", _MODES,
                        index=_MODES.index(ss.mode if ss.mode in _MODES else "Mock"),
                        key="mode_radio",
                        help="Mock = practice vs bots · Manual = you enter every "
                             "pick · ESPN = sync live from your league.")
ss.mode = mode

st.sidebar.markdown("### League")
scoring_label = st.sidebar.selectbox("Scoring", list(SCORING_LABELS.keys()), index=1)
scoring_key = SCORING_LABELS[scoring_label]
ss["_scoring_key_for_peek"] = scoring_key   # stash so the peek link can carry it
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
    "Shredder voice", value=ss.get("copilot_voice", True),
    help="Trash talk, position-run alarms, villain narration, and your squad's "
         "earned nickname. Turn off for a quiet board.")
ss.alerts_on = st.sidebar.toggle(
    "🔔 On-the-clock alert (sound + buzz)", value=ss.get("alerts_on", True),
    help="When it's YOUR pick, Shredder throws a banner, chimes, and vibrates "
         "your phone so you never miss being on the clock.")

starters = {"QB": qb, "RB": rb, "WR": wr, "TE": te, "FLEX": flex, "DST": dst, "K": k}

# PEEK MODE: the phone mirror inherits the desktop's league shape from the peek
# link, so its board/order/survival odds match the real draft exactly — no need
# to re-enter league settings on the phone.
if ss.peek_mode:
    if ss.get("_peek_teams"):
        teams = int(ss["_peek_teams"])
    if ss.get("_peek_slot"):
        slot = int(ss["_peek_slot"])
    if ss.get("_peek_rounds"):
        rounds = int(ss["_peek_rounds"])
    if ss.get("_peek_scoring"):
        scoring_key = ss["_peek_scoring"]

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
        # PEEK MODE auto-connect: the phone opened a peek link with cookies +
        # league. Silently connect (read-only) and sync so it just mirrors the
        # live draft — no Connect button, no sidebar fiddling on the phone.
        if (ss.peek_mode and ss.espn is None and have_ck
                and str(ss.get("_peek_league", "")).strip().isdigit()):
            try:
                _pcli = EC.EspnClient(int(ss["_peek_league"]),
                                      int(ss.get("_peek_season") or 2026),
                                      ss.espn_s2, ss.espn_swid)
                _ok, _msg = _pcli.verify()
                ss.espn = _pcli if _ok else None
                ss.espn_status = _msg
                if _ok:
                    _sync_espn(cfg, force=True)
            except Exception as _ex:  # noqa: BLE001
                ss.espn = None
                ss.espn_status = f"Peek connect failed: {_ex}"
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
                "Paste your two ESPN cookies below. They stay private to your "
                "session — never saved on the server.")
        with st.sidebar.expander("Paste cookies (espn_s2 + SWID)",
                                 expanded=(not have_ck)):
            if IS_CLOUD:
                st.caption("To find them: on a computer logged into "
                           "fantasy.espn.com, DevTools (F12) → Application → "
                           "Cookies → copy `espn_s2` and `SWID`.")
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

        # ONE-TAP bookmarklet demoted to Advanced (QR peek covers the common
        # phone case now, so this is only for 'connect on my phone, no computer').
        if IS_CLOUD and not have_ck:
            with st.sidebar.expander("⚙️ Advanced: one-tap login", expanded=False):
                _render_bookmarklet_setup()

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
        # OPEN ON MY PHONE: once connected, offer the read-only peek link/QR so
        # you can glance at Shredder on your phone while drafting on the computer.
        if ss.espn and not ss.peek_mode:
            _render_peek_share(cfg, st.sidebar)

# undo is available except in read-only peek mode
if not ss.peek_mode:
    st.sidebar.markdown("---")
    if st.sidebar.button("↩️ Undo last pick", use_container_width=True):
        if _undo():
            st.toast("Reverted the last pick.")
            st.rerun()


# --------------------------------------------------------------------------- gate
def _render_espn_connect_main(cfg):
    """MAIN-PAGE ESPN connect panel — so a phone user can connect end-to-end
    WITHOUT opening the sidebar (which is hard to reach on mobile). Renders the
    same one-tap setup + cookie paste + league pick + Connect, wired to the same
    session keys as the sidebar, so the two never disagree. Shown on the cloud in
    ESPN mode before a league is connected."""
    if EC is None:
        st.error("ESPN client unavailable (install `requests`).")
        return
    have_ck = bool(ss.espn_s2 and ss.espn_swid)
    st.markdown("### 🔌 Connect your ESPN league")
    if not have_ck:
        st.caption("Paste your two ESPN cookies to connect. They stay private to "
                   "your session — never stored on the server.")
        # PRIMARY path now = paste cookies (open by default). The one-tap
        # bookmarklet is tucked into 'Advanced' since QR peek covers the common
        # 'watch on my phone' case without any of this.
        s2 = st.text_input("espn_s2", type="password", value=ss.espn_s2,
                           key="m_s2")
        swid = st.text_input("SWID", type="password", value=ss.espn_swid,
                             key="m_swid")
        if st.button("Use these cookies", type="primary", key="m_useck",
                     use_container_width=True):
            ss.espn_s2, ss.espn_swid = s2, swid
            if not IS_CLOUD and SEC:
                SEC.save_file(s2, swid)
            st.rerun()
        st.caption("Where do I find these? On a computer logged into "
                   "fantasy.espn.com: DevTools (F12) → Application → Cookies → "
                   "copy `espn_s2` and `SWID`.")
        if IS_CLOUD:
            with st.expander("⚙️ Advanced: one-tap login (no computer handy)",
                             expanded=False):
                _render_bookmarklet_setup_main()
    else:
        st.success("✓ Cookies loaded. Step 2: pick your league.")
        _saved = _saved_leagues_load()
        if st.button("🔎 Find my leagues", key="m_find",
                     use_container_width=True):
            with st.spinner("Asking ESPN for your leagues…"):
                res = EC.discover_leagues(ss.espn_s2, ss.espn_swid)
            if res.get("ok") and res.get("leagues"):
                _saved_leagues_upsert(res["leagues"])
                ss.espn_status = res["message"]
                st.rerun()
            else:
                st.error(res.get("message", "Discovery failed."))
        league_id, season = "", 2026
        if _saved:
            _opts = ["— pick a league —"] + [
                (e.get("league_name") or e.get("label") or f"League {e['league_id']}")
                + f" · {e.get('season', 2026)}" for e in _saved]
            _sel = st.selectbox("Your leagues", _opts, key="m_pick")
            if _sel != _opts[0]:
                _e = _saved[_opts.index(_sel) - 1]
                league_id = str(_e["league_id"])
                season = int(_e.get("season", 2026))
        if not str(league_id).strip().isdigit():
            league_id = st.text_input("…or ESPN league ID", key="m_lid")
            season = st.number_input("Season", 2020, 2030, 2026, key="m_season")
        ss.dna_seasons = st.text_input(
            "🧬 Learn opponent DNA from seasons", value=ss.get("dna_seasons", ""),
            key="m_dna",
            help="Past seasons of THIS league. Blank = skip.")
        if st.button("⚡ Connect", type="primary", key="m_connect",
                     use_container_width=True):
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
                        st.rerun()
                except Exception as ex:  # noqa: BLE001
                    ss.espn = None
                    ss.espn_status = f"Connect failed: {ex}"
        if st.button("Forget cookies", key="m_forget"):
            ss.espn_s2 = ss.espn_swid = ""
            ss["_my_leagues"] = []
            st.rerun()
    if ss.espn_status:
        (st.success if ss.espn else st.error)(ss.espn_status)


_ready = (mode == "Manual") or (mode == "Mock" and ss.mock_on) \
    or (mode == "ESPN" and ss.espn is not None)
if not _ready and not _BETTING_ONLY:
    if mode == "Mock":
        st.info("Hit **Start / restart mock** in the sidebar to practice against "
                "AI bots that draft between your turns.")
    elif mode == "ESPN":
        # Full connect flow right here on the main page — no sidebar needed on phone.
        _render_espn_connect_main(cfg)
        st.caption("No league handy? Switch to **Mock** or **Manual** at the top.")
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
# In betting-only mode we skip the draft header + view switcher entirely; the
# betting hub renders on its own just below.
_view = "📈 Betting edge"
if not _BETTING_ONLY:
    st.caption(f"🏈 {scoring_label} · {teams}-team · your slot {slot}")

    hc = st.columns([1, 1, 1, 1])
    hc[0].metric("On the clock", "DONE" if draft_complete else f"#{ov}")
    hc[1].metric("Round", f"{rnd_now}/{int(rounds)}")
    hc[2].metric("Your next pick",
                 "—" if (draft_complete or not next_pick) else f"#{next_pick}",
                 "DONE" if draft_complete else
                 ("NOW" if is_my_turn else (f"in {picks_until}" if picks_until else "—")))
    hc[3].metric("Your players", f"{len(_mine)}/{int(rounds)}")

    # top-level view switch: draft board · rankings cheat sheet · weekly start/sit
    _VIEWS = ["🎯 Draft board", "📋 Rankings guide", "📅 Weekly lineup"]
    if LG is not None:
        _VIEWS.append("📈 Betting edge")
    _view = st.radio("View", _VIEWS,
                     horizontal=True, label_visibility="collapsed", key="top_view")

if mode == "ESPN" and ss.espn and not ss.peek_mode:
    sc = st.columns([1, 1, 4])
    if sc[0].button("🔄 Sync now"):
        _sync_espn(cfg, force=True)
        st.rerun()
    auto = sc[1].toggle("Auto-sync", value=False)
    if ss.get("sync_note"):
        sc[2].caption(ss.sync_note)
    # ON THE CLOCK: big banner + chime + vibrate the instant it's your pick.
    _render_on_the_clock_alert(cfg)
    if auto:
        _sync_espn(cfg)
        import time as _t
        _t.sleep(4)
        st.rerun()

# PEEK MODE: the phone mirror. Show a small read-only banner, keep pulling the
# live draft from ESPN, and auto-refresh so picks appear on their own — no taps.
if ss.peek_mode:
    if ss.espn:
        pc = st.columns([3, 1])
        pc[0].info("👀 **Peek mode** — read-only mirror of your live draft. "
                   "Updates on its own; draft on your computer.")
        ss.alerts_on = pc[1].toggle("🔔 Alerts", value=ss.get("alerts_on", True),
                                    key="peek_alerts",
                                    help="Sound + vibrate when you're on the clock.")
        _sync_espn(cfg)
        # The payoff: buzz + banner on the phone the moment YOU'RE on the clock.
        _render_on_the_clock_alert(cfg)
        # Auto-refresh so new picks appear on their own — only under a live server
        # (guarded so the test harness doesn't loop forever).
        if _is_live_runtime():
            import time as _t
            _t.sleep(6)
            st.rerun()
    else:
        st.warning("👀 Peek mode couldn't connect to the draft. "
                   + (ss.get("espn_status") or "Re-open the link from your computer."))


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


def _render_betting_edge():
    """Betting hub. All the model-vs-market tooling, split into tabs so it's not
    one long scroll: the live Slate (our win prob vs the book), the multi-book
    Line shop & player props, and the Model report card (calibration). Purely a
    divergence read; not betting advice."""
    st.markdown("#### 📈 Betting edge")
    _tab_slate, _tab_shop, _tab_card = st.tabs(
        ["🎯 Slate", "🛒 Line shop & props", "📊 Report card"])
    with _tab_slate:
        _render_slate()
    with _tab_shop:
        _render_line_shop()
    with _tab_card:
        _render_report_card()


def _render_slate():
    """Model-vs-market read on today's NFL slate. For each game we compute our
    OWN live win probability and compare it to the de-vigged book odds — where
    they DISAGREE is the opportunity. Plus an Upset Radar for chalk in trouble."""
    st.markdown("##### Our model vs the market")
    st.caption("We compute each game's win probability from the live state, then "
               "strip the book's vig from the moneylines for a fair comparison. "
               "A gap between the two is where the market and the model disagree. "
               "Informational only — not betting advice.")

    if LG is None:
        st.info("Live-games module unavailable (needs the `requests` package).")
        return

    if st.button("🔄 Refresh slate"):
        st.rerun()

    with st.spinner("Pulling the live NFL slate…"):
        try:
            games = LG.fetch_live_games()
        except Exception as ex:  # noqa: BLE001
            st.error(f"Couldn't reach the scoreboard: {ex}")
            return

    # log model-vs-market snapshots for later calibration (best-effort, silent)
    if PH is not None and games:
        try:
            PH.snapshot(games)
        except Exception:  # noqa: BLE001
            pass

    if not games:
        st.info("No games on the board right now (off-day, or the slate hasn't "
                "posted). Check back on game day — this fills in with live win "
                "probabilities and edges once games are scheduled.")
        return

    live_n = sum(1 for g in games if g.status == "in")
    edges = [g for g in games if abs(g.edge_fav) >= 0.05
             and g.model_p_fav is not None]
    hot = [g for g in games if g.upset_heat >= 40 and g.status == "in"]

    mc = st.columns(3)
    mc[0].metric("Games", len(games))
    mc[1].metric("Live now", live_n)
    mc[2].metric("Edges flagged", len(edges))

    # ---- Upset Radar (chalk in trouble) ----
    if hot:
        st.markdown("##### 🚨 Upset radar")
        for g in sorted(hot, key=lambda x: -x.upset_heat):
            st.markdown(
                f'<div class="insight i-cliff"><div class="it">{g.upset_note}</div>'
                f'<div class="ib">heat {int(g.upset_heat)}/100 · {g.away} '
                f'{g.away_score} @ {g.home} {g.home_score}</div></div>',
                unsafe_allow_html=True)

    # ---- Per-game edge table ----
    st.markdown("##### Slate")
    # value edges first, then live, then the rest
    games_sorted = sorted(
        games, key=lambda g: (abs(g.edge_fav) < 0.05, g.status != "in",
                              -abs(g.edge_fav)))
    for g in games_sorted:
        head = f"{g.away} {g.away_score} @ {g.home} {g.home_score}"
        line = g.spread or "—"
        with st.container(border=True):
            c = st.columns([2.4, 1, 1, 1.3])
            c[0].markdown(
                f'<b>{head}</b><br><span class="pmeta">{g.detail} · line {line}'
                + (f" · O/U {g.over_under}" if g.over_under else "")
                + '</span>', unsafe_allow_html=True)
            if g.model_p_fav is not None:
                c[1].markdown(
                    f'<div class="pmeta">MODEL</div>'
                    f'<b>{int(g.model_p_fav*100)}%</b>', unsafe_allow_html=True)
                c[2].markdown(
                    f'<div class="pmeta">MARKET</div>'
                    f'<b>{int((g.market_p_fav or 0)*100)}%</b>',
                    unsafe_allow_html=True)
                if abs(g.edge_fav) >= 0.05 and g.edge_note:
                    _cls = "vr-value" if "FAVORITE" in g.edge_note else "vr-reach"
                    c[3].markdown(
                        f'<span class="vr {_cls}">{g.edge_note} '
                        f'{"+" if g.edge_fav>=0 else ""}{int(g.edge_fav*100)}%</span>',
                        unsafe_allow_html=True)
                else:
                    c[3].markdown('<span class="pmeta">no clear edge</span>',
                                  unsafe_allow_html=True)
            else:
                c[1].markdown('<span class="pmeta">no line yet</span>',
                              unsafe_allow_html=True)
            # possession / drives-left (live games only)
            if g.possession_note:
                _ball = (f"🏈 {g.possessing_team} has the ball · "
                         if g.possessing_team else "")
                _fd = g.fav_drives_left
                _dd = g.dog_drives_left
                _drv = ""
                if _fd is not None and _dd is not None:
                    _drv = (f"{g.favorite} ~{_fd} drives left vs "
                            f"~{_dd} for the other side")
                st.markdown(
                    f'<div class="pmeta">{_ball}{_drv}</div>',
                    unsafe_allow_html=True)
    st.caption("MODEL = our live win prob for the favorite · MARKET = the same "
               "from de-vigged book odds · edge = model − market. When a game is "
               "live, the model is possession-aware: it counts each team's "
               "remaining scoring drives (who has the ball + drive-pace) instead "
               "of raw clock.")


def _fmt_ml(ml):
    """American moneyline -> display string with sign."""
    if ml is None:
        return "—"
    ml = int(ml)
    return f"+{ml}" if ml > 0 else str(ml)


def _render_line_shop():
    """Compare DraftKings / FanDuel / BetMGM on each game (best price + book-vs-
    consensus divergence), then drill into player props for a chosen game. Powered
    by The Odds API; needs an API key. Informational only — not betting advice."""
    st.markdown("##### Line shop & player props")

    if OF is None:
        st.info("Odds module unavailable.")
        return
    if not OF.configured():
        st.info("No Odds API key set. Add one to `.streamlit/secrets.toml` "
                "under `[odds_api] api_key = \"…\"` (get a free key at "
                "the-odds-api.com) to compare DraftKings / FanDuel / BetMGM and "
                "pull player props.")
        return

    st.caption("Same game across DraftKings, FanDuel and BetMGM. BEST = the book "
               "paying the most on each side (line-shopping). DIVERGENCE = a book "
               "pricing a side away from the market consensus. Player props are "
               "opt-in per game (each pull uses a few API credits).")

    if st.button("🔄 Refresh odds (uses API credits)"):
        st.session_state.pop("_odds_games", None)
        st.session_state.pop("_odds_props", None)
        st.rerun()

    if "_odds_games" not in st.session_state:
        with st.spinner("Pulling multi-book odds…"):
            try:
                st.session_state["_odds_games"] = OF.fetch_game_odds()
            except Exception as ex:  # noqa: BLE001
                st.error(f"Couldn't reach the odds API: {ex}")
                return
    games = st.session_state.get("_odds_games") or []

    q = OF.last_quota
    if q.get("remaining") is not None:
        st.caption(f"API credits remaining: {q['remaining']} "
                   f"(last call cost {q.get('last_cost')})")

    if not games:
        st.info("No games returned (off-season, or the slate hasn't posted).")
        return

    # ---- per-game line-shop table ----
    st.markdown("##### Game odds — best price by book (current week)")
    for g in games:
        with st.container(border=True):
            head = f"{g.away} @ {g.home}"
            sub = (f"fav {g.favorite}" if g.favorite else "")
            if g.consensus_p_fav is not None:
                sub += f" · consensus {int(g.consensus_p_fav*100)}% to win"
            st.markdown(f"<b>{head}</b><br><span class='pmeta'>{sub}</span>",
                        unsafe_allow_html=True)
            cc = st.columns(2)
            cc[0].markdown(
                f"<div class='pmeta'>BEST ON {g.favorite}</div>"
                f"<b>{_fmt_ml(g.best_fav_ml)}</b> "
                f"<span class='pmeta'>@ {g.best_fav_ml_book or '—'}</span>",
                unsafe_allow_html=True)
            cc[1].markdown(
                f"<div class='pmeta'>BEST ON {g.dog}</div>"
                f"<b>{_fmt_ml(g.best_dog_ml)}</b> "
                f"<span class='pmeta'>@ {g.best_dog_ml_book or '—'}</span>",
                unsafe_allow_html=True)
            if g.divergence_note:
                st.markdown(f"<div class='pmeta'>↔ {g.divergence_note}</div>",
                            unsafe_allow_html=True)

    # ---- player props drill-down ----
    st.markdown("##### Player props")
    labels = [f"{g.away} @ {g.home}" for g in games]
    choice = st.selectbox("Pick a game to pull props (uses API credits)",
                          ["— select —"] + labels, key="_prop_pick")
    if choice and choice != "— select —":
        gi = labels.index(choice)
        g = games[gi]
        cache = st.session_state.setdefault("_odds_props", {})
        if g.event_id not in cache:
            with st.spinner(f"Pulling props for {choice}…"):
                try:
                    cache[g.event_id] = OF.fetch_player_props(g.event_id)
                except Exception as ex:  # noqa: BLE001
                    st.error(f"Couldn't pull props: {ex}")
                    cache[g.event_id] = []
        props = cache.get(g.event_id) or []
        if not props:
            st.info("No props posted for this game yet (yardage lines usually "
                    "open closer to kickoff; anytime-TD markets post first).")
        else:
            # group by market for readability
            by_market: dict[str, list] = {}
            for pp in props:
                by_market.setdefault(pp.market_label, []).append(pp)
            for mlabel in sorted(by_market):
                st.markdown(f"**{mlabel}**")
                for pp in by_market[mlabel][:40]:
                    line = (f"line {pp.consensus_point:g}"
                            if pp.consensus_point is not None else "")
                    over = (f"O {_fmt_ml(pp.best_over_price)}@{pp.best_over_book}"
                            if pp.best_over_price is not None else "")
                    under = (f"U {_fmt_ml(pp.best_under_price)}@{pp.best_under_book}"
                             if pp.best_under_price is not None else "")
                    bits = " · ".join(b for b in (line, over, under) if b)
                    div = (f" · ↔ {pp.divergence_note}"
                           if pp.divergence_note else "")
                    st.markdown(
                        f"<div class='pmeta'><b>{pp.player}</b> — {bits}{div}</div>",
                        unsafe_allow_html=True)


def _render_report_card():
    """How well has our model tracked reality vs the market? Brier score is the
    north star: if the de-vigged market beats our model, the edges we flag are
    noise. Calibration shows whether 'we said 70%' actually meant ~70%."""
    st.markdown("##### Model report card — are our edges real?")

    if PH is None:
        st.info("History module unavailable.")
        return

    stx = PH.stats()
    st.caption(
        f"Logged {stx['total_records']} snapshots across {stx['distinct_events']} "
        f"games · {stx['settled_records']} settled with final scores. We log the "
        "model-vs-market probability every time this view refreshes, then grade "
        "it once games finish. Not betting advice.")

    if st.button("✅ Settle finished games"):
        try:
            n = PH.settle()
            st.success(f"Settled {n} newly-final game record(s).")
        except Exception as ex:  # noqa: BLE001
            st.error(f"Couldn't settle: {ex}")
        st.rerun()

    kind = st.radio("Scope", ["all", "pregame", "live", "poss_aware"],
                    horizontal=True, key="_rc_kind",
                    format_func=lambda k: {"all": "All", "pregame": "Pregame",
                                           "live": "Live", "poss_aware":
                                           "Possession-aware"}[k])
    rep = PH.score(kind)

    if rep["n"] == 0:
        st.info("No settled games yet. Once games finish and you hit "
                "‘Settle finished games’, the model gets graded here — Brier "
                "score, calibration, and whether it beats the market.")
        return

    # headline: model vs market Brier
    mc = st.columns(3)
    mc[0].metric("Settled games", rep["n"])
    mc[1].metric("Model Brier", rep["model_brier"],
                 help="Mean squared error of our P(fav) vs outcome. Lower is better.")
    mc[2].metric("Market Brier", rep["market_brier"],
                 delta=(f"{rep['brier_delta']:+.3f} vs model"
                        if rep["brier_delta"] is not None else None),
                 delta_color="inverse",
                 help="Same, for the de-vigged book line. The number to beat.")

    _beat = (rep["model_brier"] is not None and rep["market_brier"] is not None
             and rep["model_brier"] < rep["market_brier"])
    _cls = "vr-value" if _beat else "vr-reach"
    st.markdown(f'<span class="vr {_cls}">{rep["verdict"]}</span>',
                unsafe_allow_html=True)

    # calibration table
    if rep["calibration"]:
        st.markdown("##### Calibration — did our % mean what it said?")
        st.caption("For each model-probability bucket: what we predicted on "
                   "average vs how often the favorite actually won. Close = "
                   "well-calibrated; a big gap = the model is over/under-confident "
                   "in that range.")
        import pandas as _pd
        df = _pd.DataFrame(rep["calibration"])
        df = df.rename(columns={"bucket": "Model says", "n": "Games",
                                "predicted": "Predicted", "actual": "Actual won",
                                "gap": "Gap (actual−pred)"})
        st.dataframe(df, hide_index=True, use_container_width=True)

    st.caption("This measures the model; it does not retrain it. Once enough "
               "games accumulate, the calibration gaps tell us which win-prob "
               "constants to re-tune.")


# Betting-only mode (from the welcome fork): the functions exist by now, so
# render the hub and stop — the draft board/setup above was skipped for it.
if _BETTING_ONLY:
    _render_betting_edge()
    st.stop()

if _view == "📈 Betting edge":
    _render_betting_edge()
    st.stop()

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
# In PEEK mode the phone is read-only — you draft on the computer, the phone just
# mirrors it — so the manual "mark someone gone / I drafted them" controls hide.
if not ss.peek_mode:
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
        if not ss.peek_mode:
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
            if not ss.peek_mode:
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
