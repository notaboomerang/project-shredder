"""
Multi-book odds feed — The Odds API (DraftKings / FanDuel / BetMGM).

Where live_games.py gives you ESPN's single-provider line, this module pulls the
SAME game across multiple sportsbooks so you can line-shop and spot divergence,
plus PLAYER PROPS (pass/rush/reception yards, receptions, TDs, etc.) that ESPN's
scoreboard doesn't carry at all.

Three opportunity signals, from most to least model-dependent:

  1. MODEL vs MARKET (games)     — our win_prob model vs the consensus de-vigged
     book probability. A gap is where our model disagrees with the market.
  2. LINE SHOP (games + props)   — which book offers the best number/price on a
     given side. Book-independent: pure "where do I get paid most."
  3. BOOK vs CONSENSUS (games+props) — a book that is an OUTLIER from the market
     consensus. Doesn't need our model to be right; the market flags itself.

Config: set ODDS_API_KEY in the environment or in Streamlit secrets
(.streamlit/secrets.toml -> [odds_api] api_key = "..."). Without a key, every
call returns [] and the UI degrades gracefully (ESPN feed still works).

Quota: The Odds API charges 1 credit per region per market for /odds, and per
market for per-event /events/{id}/odds (props). We track the x-requests-*
headers so the UI can show remaining credits. Free tier is ~500/month, so props
(one call per game) are opt-in.

Nothing here is betting advice — it's a divergence/line-shop read.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import win_prob as WP

try:
    from curl_cffi import requests as _cffi  # reuse the TLS-impersonation client
    _HAS_CFFI = True
except Exception:
    _cffi = None  # type: ignore
    _HAS_CFFI = False

try:
    import requests as _requests
except Exception:
    _requests = None  # type: ignore


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_HOST = "https://api.the-odds-api.com"
_SPORT = "americanfootball_nfl"
_REGION = "us"
_BOOKS = ["draftkings", "fanduel", "betmgm"]

# Featured game markets (cheap): moneyline, spread, total.
_GAME_MARKETS = ["h2h", "spreads", "totals"]

# NFL player prop markets we care about (per-event endpoint; each costs quota).
_PROP_MARKETS = [
    "player_pass_yds", "player_pass_tds", "player_rush_yds",
    "player_reception_yds", "player_receptions", "player_anytime_td",
]

# Friendly names for the UI.
_PROP_LABEL = {
    "player_pass_yds": "Pass Yds",
    "player_pass_tds": "Pass TDs",
    "player_rush_yds": "Rush Yds",
    "player_reception_yds": "Rec Yds",
    "player_receptions": "Receptions",
    "player_anytime_td": "Anytime TD",
}

# populated after each API call so the UI can show usage
last_quota: dict = {"remaining": None, "used": None, "last_cost": None}


def api_key() -> Optional[str]:
    """Resolve the Odds API key from env or Streamlit secrets. None if unset."""
    k = os.environ.get("ODDS_API_KEY")
    if k:
        return k.strip()
    try:
        import streamlit as st  # type: ignore
        # support [odds_api] api_key = "..."  or a top-level odds_api_key
        if "odds_api" in st.secrets and "api_key" in st.secrets["odds_api"]:
            return str(st.secrets["odds_api"]["api_key"]).strip()
        if "odds_api_key" in st.secrets:
            return str(st.secrets["odds_api_key"]).strip()
    except Exception:
        pass
    return None


def configured() -> bool:
    return bool(api_key())


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _get(path: str, params: dict) -> Optional[list | dict]:
    """GET against The Odds API. Returns parsed JSON (list/dict) or None.
    Records quota headers into last_quota."""
    key = api_key()
    if not key:
        return None
    url = _HOST + path
    p = dict(params)
    p["apiKey"] = key
    try:
        if _HAS_CFFI and _cffi is not None:
            r = _cffi.get(url, params=p, timeout=15, impersonate="chrome")
        elif _requests is not None:
            r = _requests.get(url, params=p, timeout=15)
        else:
            return None
        # record quota (headers are case-insensitive on both clients)
        h = r.headers
        last_quota["remaining"] = h.get("x-requests-remaining")
        last_quota["used"] = h.get("x-requests-used")
        last_quota["last_cost"] = h.get("x-requests-last")
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class BookLine:
    """One book's take on a game."""
    book: str
    fav_ml: Optional[float] = None     # American moneyline for the favorite
    dog_ml: Optional[float] = None
    spread: Optional[float] = None     # favorite's spread magnitude (positive)
    total: Optional[float] = None      # over/under points


@dataclass
class GameOdds:
    event_id: str
    home: str
    away: str
    commence: str
    favorite: str = ""                 # abbrev/name favored (from spread sign)
    dog: str = ""
    books: list[BookLine] = field(default_factory=list)

    # consensus + signals (filled by _score_game)
    consensus_p_fav: Optional[float] = None    # avg de-vigged P(fav) across books
    model_p_fav: Optional[float] = None        # our model (pregame from spread)
    edge_fav: float = 0.0
    edge_note: str = ""
    best_fav_ml_book: str = ""                 # line-shop: best price on the fav
    best_fav_ml: Optional[float] = None
    best_dog_ml_book: str = ""
    best_dog_ml: Optional[float] = None
    divergence_note: str = ""                  # book-vs-consensus outlier note

    # situational POWER PLAY: Q4 clutch signal confirming the model edge
    power_play: bool = False
    power_play_side: str = ""                  # "FAVORITE" | "UNDERDOG" | ""
    power_play_strength: str = ""              # "STRONG" | "LEAN" | ""
    power_play_note: str = ""


@dataclass
class PropOutcome:
    """One book's line on one player prop (over/under or yes/no)."""
    book: str
    point: Optional[float]             # the line (e.g. 62.5 yards); None for TD y/n
    over_price: Optional[float]        # American odds for Over / Yes
    under_price: Optional[float]       # American odds for Under / No


@dataclass
class PlayerProp:
    event_id: str
    player: str
    market: str                        # market key
    market_label: str
    outcomes: list[PropOutcome] = field(default_factory=list)

    consensus_point: Optional[float] = None    # median line across books
    best_over_book: str = ""
    best_over_price: Optional[float] = None
    best_under_book: str = ""
    best_under_price: Optional[float] = None
    divergence_note: str = ""                  # a book off the consensus line
    matchup_note: str = ""                     # defense-vs-position read (Tier A)
    matchup_softness: str = ""                 # "SOFT" | "TOUGH" | "NEUTRAL" | ""
    matchup_lean: Optional[float] = None       # ± fantasy-pts nudge from the D


# ---------------------------------------------------------------------------
# Game odds
# ---------------------------------------------------------------------------

def _better_ml(a: Optional[float], b: Optional[float]) -> bool:
    """True if American moneyline `a` pays MORE than `b` (better for bettor).
    +150 beats +120; -110 beats -130; any positive beats any negative."""
    if a is None:
        return False
    if b is None:
        return True
    return a > b


def _parse_game(ev: dict) -> Optional[GameOdds]:
    home = ev.get("home_team", "") or ""
    away = ev.get("away_team", "") or ""
    g = GameOdds(event_id=ev.get("id", ""), home=home, away=away,
                 commence=ev.get("commence_time", ""))

    # tally spread sign to decide favorite; collect each book's numbers
    fav_votes: dict[str, int] = {}
    for bk in ev.get("bookmakers", []) or []:
        bkey = bk.get("key", "")
        line = BookLine(book=bkey)
        for mk in bk.get("markets", []) or []:
            mkey = mk.get("key")
            outs = mk.get("outcomes", []) or []
            if mkey == "h2h":
                for o in outs:
                    nm, price = o.get("name"), o.get("price")
                    if nm == home:
                        line._home_ml = price  # type: ignore[attr-defined]
                    elif nm == away:
                        line._away_ml = price  # type: ignore[attr-defined]
            elif mkey == "spreads":
                for o in outs:
                    pt = o.get("point")
                    if pt is not None and pt < 0:  # negative point = favorite
                        line.spread = abs(float(pt))
                        fav_votes[o.get("name", "")] = fav_votes.get(
                            o.get("name", ""), 0) + 1
            elif mkey == "totals":
                for o in outs:
                    if o.get("name") == "Over" and o.get("point") is not None:
                        line.total = float(o.get("point"))
        g.books.append(line)

    # favorite = most-voted spread favorite (fallback: home)
    g.favorite = (max(fav_votes, key=fav_votes.get) if fav_votes else home)
    g.dog = away if g.favorite == home else home

    # now map each book's home/away ml to fav/dog
    for line in g.books:
        hml = getattr(line, "_home_ml", None)
        aml = getattr(line, "_away_ml", None)
        if g.favorite == home:
            line.fav_ml, line.dog_ml = hml, aml
        else:
            line.fav_ml, line.dog_ml = aml, hml
    return g if g.books else None


def _score_game(g: GameOdds) -> None:
    """Fill consensus, model edge, line-shop best prices, and divergence."""
    # consensus de-vigged P(fav) across books that have both moneylines
    probs = []
    for line in g.books:
        p = WP.devig(WP.american_to_prob(line.fav_ml),
                     WP.american_to_prob(line.dog_ml))
        if p is not None:
            probs.append(p)
        # line-shop best prices
        if _better_ml(line.fav_ml, g.best_fav_ml):
            g.best_fav_ml, g.best_fav_ml_book = line.fav_ml, line.book
        if _better_ml(line.dog_ml, g.best_dog_ml):
            g.best_dog_ml, g.best_dog_ml_book = line.dog_ml, line.book
    if probs:
        g.consensus_p_fav = round(sum(probs) / len(probs), 3)

    # our model (pregame: from the consensus spread)
    spreads = [l.spread for l in g.books if l.spread is not None]
    if spreads:
        favpts = sum(spreads) / len(spreads)
        g.model_p_fav = round(WP.spread_to_prob(favpts), 3)
        if g.consensus_p_fav is not None:
            g.edge_fav, g.edge_note = WP.edge(g.model_p_fav, g.consensus_p_fav)

    # book-vs-consensus divergence: biggest de-vigged prob gap from consensus
    if g.consensus_p_fav is not None and len(probs) >= 2:
        worst_book, worst_gap = "", 0.0
        for line in g.books:
            p = WP.devig(WP.american_to_prob(line.fav_ml),
                         WP.american_to_prob(line.dog_ml))
            if p is None:
                continue
            gap = p - g.consensus_p_fav
            if abs(gap) > abs(worst_gap):
                worst_gap, worst_book = gap, line.book
        if worst_book and abs(worst_gap) >= 0.03:
            side = g.favorite if worst_gap < 0 else g.dog
            # if a book prices the fav LOWER than consensus, it's long the dog ->
            # best value on the fav is elsewhere; note the outlier + cheap side
            g.divergence_note = (
                f"{worst_book} is {abs(worst_gap)*100:.0f}% off consensus "
                f"(cheapest on {side})")

    # POWER PLAY: does the situational Q4 clutch read confirm the model edge?
    # Independent signal (game-script history) reinforcing the model-vs-market
    # gap = higher conviction. Guarded so a missing/failing clutch module never
    # breaks the odds feed.
    if g.edge_fav and abs(g.edge_fav) >= 0.05:
        try:
            import clutch_split as CS
            pp = CS.power_play_signal(g.home, g.away, g.favorite, g.edge_fav)
            if pp.get("power_play"):
                g.power_play = True
                g.power_play_side = pp.get("side", "")
                g.power_play_strength = pp.get("strength", "")
                g.power_play_note = pp.get("note", "")
        except Exception:
            pass


def _parse_iso(ts: str):
    """Parse an ISO8601 UTC timestamp to an aware datetime, or None."""
    if not ts:
        return None
    import datetime as _dt
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _current_week_window(commence_times: list):
    """Given all games' commence datetimes, return (start, end) bounding the
    NFL week closest to now (the current in-progress week, else the next one).

    NFL weeks span Thu -> Mon night (with occasional Fri/Sat/international games),
    so we anchor on the EARLIEST game that is still upcoming or within the last
    ~2 days (to keep a week that's mid-progress), then take a 6-day window from
    that anchor's calendar day. That captures Thu-through-the-following-Wed.
    """
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    times = sorted(t for t in commence_times if t is not None)
    if not times:
        return None, None
    # anchor = first game that hasn't finished (started < ~4h ago still counts
    # as "this week"); else fall back to the very next game.
    grace = _dt.timedelta(hours=4)
    upcoming = [t for t in times if t >= now - grace]
    anchor = upcoming[0] if upcoming else times[0]
    # window start = anchor's UTC calendar day at 00:00; end = +6 days 23:59.
    start = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + _dt.timedelta(days=6, hours=23, minutes=59)
    return start, end


def fetch_game_odds(books: Optional[list[str]] = None,
                    current_week_only: bool = True) -> list[GameOdds]:
    """Multi-book NFL game odds with model-edge, line-shop, and divergence.

    By default returns only the current NFL week (the slate closest to today,
    in progress or next up). Set current_week_only=False for the full upcoming
    board. Returns [] if no API key or on any failure.
    """
    data = _get(f"/v4/sports/{_SPORT}/odds", {
        "regions": _REGION,
        "markets": ",".join(_GAME_MARKETS),
        "oddsFormat": "american",
        "bookmakers": ",".join(books or _BOOKS),
    })
    if not isinstance(data, list):
        return []

    # optionally clip to the current/nearest week by commence_time
    if current_week_only:
        times = [_parse_iso(ev.get("commence_time", "")) for ev in data]
        start, end = _current_week_window(times)
        if start is not None:
            data = [ev for ev in data
                    if (dt := _parse_iso(ev.get("commence_time", ""))) is not None
                    and start <= dt <= end]

    out: list[GameOdds] = []
    for ev in data:
        g = _parse_game(ev)
        if g:
            _score_game(g)
            out.append(g)
    # earliest kickoff first
    out.sort(key=lambda g: g.commence or "")
    return out


# ---------------------------------------------------------------------------
# Player props
# ---------------------------------------------------------------------------

def _median(vals: list[float]) -> Optional[float]:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def _parse_props(ev: dict, event_id: str) -> list[PlayerProp]:
    """Per-event odds -> one PlayerProp per (player, market), lines across books."""
    # index by (player, market)
    idx: dict[tuple[str, str], PlayerProp] = {}
    for bk in ev.get("bookmakers", []) or []:
        bkey = bk.get("key", "")
        for mk in bk.get("markets", []) or []:
            mkey = mk.get("key", "")
            if mkey not in _PROP_MARKETS:
                continue
            # group outcomes by player (the 'description' field on props)
            by_player: dict[str, dict] = {}
            for o in mk.get("outcomes", []) or []:
                player = o.get("description") or o.get("name") or ""
                nm = (o.get("name") or "").lower()
                slot = by_player.setdefault(player, {})
                if nm in ("over", "yes"):
                    slot["over"] = o.get("price")
                    slot["point"] = o.get("point", slot.get("point"))
                elif nm in ("under", "no"):
                    slot["under"] = o.get("price")
                    slot["point"] = o.get("point", slot.get("point"))
                else:
                    slot["point"] = o.get("point", slot.get("point"))
            for player, slot in by_player.items():
                k = (player, mkey)
                pp = idx.get(k)
                if pp is None:
                    pp = PlayerProp(event_id=event_id, player=player,
                                    market=mkey,
                                    market_label=_PROP_LABEL.get(mkey, mkey))
                    idx[k] = pp
                pp.outcomes.append(PropOutcome(
                    book=bkey, point=slot.get("point"),
                    over_price=slot.get("over"), under_price=slot.get("under")))

    props = list(idx.values())
    for pp in props:
        _score_prop(pp)
    return props


def _score_prop(pp: PlayerProp) -> None:
    """Consensus line + best over/under price + line divergence for one prop."""
    pts = [o.point for o in pp.outcomes if o.point is not None]
    pp.consensus_point = _median(pts) if pts else None

    for o in pp.outcomes:
        if _better_ml(o.over_price, pp.best_over_price):
            pp.best_over_price, pp.best_over_book = o.over_price, o.book
        if _better_ml(o.under_price, pp.best_under_price):
            pp.best_under_price, pp.best_under_book = o.under_price, o.book

    # divergence: a book whose LINE differs most from the consensus point
    if pp.consensus_point is not None and len(pts) >= 2:
        worst_book, worst_gap = "", 0.0
        for o in pp.outcomes:
            if o.point is None:
                continue
            gap = o.point - pp.consensus_point
            if abs(gap) > abs(worst_gap):
                worst_gap, worst_book = gap, o.book
        if worst_book and abs(worst_gap) >= 0.5:
            softer = "higher" if worst_gap > 0 else "lower"
            pp.divergence_note = (
                f"{worst_book} line {softer} by {abs(worst_gap):g} "
                f"(consensus {pp.consensus_point:g})")


# ---------------------------------------------------------------------------
# Player-prop matchup enrichment (Tier A: defense vs position)
# ---------------------------------------------------------------------------

# Map a prop market key to the fantasy position the OPPOSING defense is graded
# against. Reception/receiving markets are pass-catchers — we default them to WR
# but let a supplied position resolver override per-player (a TE or pass-catching
# RB gets its own defense-vs-position read).
_MARKET_POSITION = {
    "player_pass_yds": "QB",
    "player_pass_tds": "QB",
    "player_pass_attempts": "QB",
    "player_rush_yds": "RB",
    "player_rush_attempts": "RB",
    "player_reception_yds": "WR",
    "player_receptions": "WR",
    "player_anytime_td": None,          # position-agnostic; use resolver only
}


def enrich_props_with_matchup(props, home, away, favorite=None,
                              team_of=None, pos_of=None, reception=0.5):
    """Attach a Tier-A defense-vs-position matchup nudge to each prop, IN PLACE.

    The prop objects carry only a player NAME and a market, so the caller
    supplies two light resolvers built from its own player pool:
      • team_of(name) -> team abbrev ("KC")   — to find the player's team, hence
        the OPPONENT defense (the other side of this game).
      • pos_of(name)  -> "QB"|"RB"|"WR"|"TE"   — overrides the market's default
        position (so a pass-catching RB / TE gets its own read).

    Both are optional; when a player's team can't be resolved we skip the nudge
    for that prop (never guess). Degrades silently if defense_vs_position or its
    data is unavailable. Returns the same list.
    """
    try:
        import defense_vs_position as DVP
    except Exception:
        return props

    home_u = (home or "").upper()
    away_u = (away or "").upper()
    team_of = team_of or (lambda _n: None)
    pos_of = pos_of or (lambda _n: None)

    for pp in props:
        try:
            player_team = (team_of(pp.player) or "").upper()
            if player_team not in (home_u, away_u):
                continue                        # can't place the player — skip
            opponent = away_u if player_team == home_u else home_u

            # position: explicit resolver first, else the market's default
            pos = (pos_of(pp.player) or "").upper() or _MARKET_POSITION.get(pp.market)
            if not pos:
                continue                        # e.g. anytime-TD with no resolver

            nudge = DVP.matchup_nudge(opponent, pos, reception=reception)
            if nudge.softness in ("SOFT", "TOUGH"):
                pp.matchup_note = nudge.note
                pp.matchup_softness = nudge.softness
                pp.matchup_lean = nudge.lean
            elif nudge.source == "pbp":
                pp.matchup_note = nudge.note
                pp.matchup_softness = "NEUTRAL"
                pp.matchup_lean = nudge.lean
        except Exception:
            continue
    return props


def fetch_player_props(event_id: str,
                       markets: Optional[list[str]] = None,
                       books: Optional[list[str]] = None) -> list[PlayerProp]:
    """Player props for ONE game (per-event endpoint; costs quota per market).
    Returns [] if no key or on failure."""
    data = _get(f"/v4/sports/{_SPORT}/events/{event_id}/odds", {
        "regions": _REGION,
        "markets": ",".join(markets or _PROP_MARKETS),
        "oddsFormat": "american",
        "bookmakers": ",".join(books or _BOOKS),
    })
    if not isinstance(data, dict):
        return []
    return _parse_props(data, event_id)


# ---------------------------------------------------------------------------
# Upcoming events (cheap; no quota) — to list game ids for prop lookups
# ---------------------------------------------------------------------------

def fetch_events() -> list[dict]:
    """List upcoming/in-play NFL events (id, teams, commence). Free (no quota)."""
    data = _get(f"/v4/sports/{_SPORT}/events", {})
    return data if isinstance(data, list) else []
