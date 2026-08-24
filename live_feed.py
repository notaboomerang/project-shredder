"""
Live projections + ADP fetcher (FantasyPros consensus).

Scrapes the free FantasyPros per-position projection pages and the ADP page,
converts to our RawPlayer stat-line format, and returns a full pool. Any
failure (network, layout change, parse error) is swallowed so the caller can
fall back to the seed — this feed must NEVER block a live draft.

Projection pages (draft week, per position):
  https://www.fantasypros.com/nfl/projections/{qb,rb,wr,te,k,dst}.php?week=draft
ADP (per format):
  https://www.fantasypros.com/nfl/adp/{overall,ppr,half-point-ppr,standard}.php

We fetch STANDARD projections (the raw stat lines) and let engine.project_points
re-score them for any format — so one fetch serves PPR / half / standard.
"""
from __future__ import annotations

import re
from typing import Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FantasyDraftAssistant/1.0"
_BASE = "https://www.fantasypros.com/nfl"
_PROJ = _BASE + "/projections/{pos}.php?week=draft"
_ADP = {
    "ppr": _BASE + "/adp/ppr-overall.php",
    "half": _BASE + "/adp/half-point-ppr-overall.php",
    "std": _BASE + "/adp/standard-overall.php",
    "overall": _BASE + "/adp/overall.php",
}

# FantasyFootballCalculator ADP JSON API (plain requests, no browser needed).
# Returns real consensus ADP + bye per player, per format & team count.
_FFC = "https://fantasyfootballcalculator.com/api/v1/adp/{fmt}?teams={teams}&year={year}"
_FFC_FMT = {"ppr": "ppr", "half": "half-ppr", "std": "standard"}

# Column order per position on the projection tables (after the Player cell).
# Values map to our stat-line keys; None = skip that column.
_COLS = {
    "qb":  ["pass_att_c", "pass_cmp_c", "pass_yd", "pass_td", "int",
            "rush_att_c", "rush_yd", "rush_td", "fumble_lost", "_fpts"],
    "rb":  ["rush_att_c", "rush_yd", "rush_td", "rec", "rec_yd", "rec_td",
            "fumble_lost", "_fpts"],
    "wr":  ["rec", "rec_yd", "rec_td", "rush_att_c", "rush_yd", "rush_td",
            "fumble_lost", "_fpts"],
    "te":  ["rec", "rec_yd", "rec_td", "fumble_lost", "_fpts"],
}
_STAT_KEYS = {"pass_yd", "pass_td", "int", "rush_yd", "rush_td",
              "rec", "rec_yd", "rec_td", "fumble_lost"}

# rows look like: <td class="player-label ..."><a ...>Name</a> <small>TEAM</small></td>
_ROW_RE = re.compile(
    r'<tr[^>]*>\s*<td[^>]*class="[^"]*player-label[^"]*"[^>]*>(.*?)</td>(.*?)</tr>',
    re.DOTALL | re.IGNORECASE)
_NAME_RE = re.compile(r'<a[^>]*>(.*?)</a>', re.DOTALL)
# team may be in <small>TEAM</small>, in (TEAM), or inline after the name link
_TEAM_RE = re.compile(
    r'<small[^>]*>\s*([A-Z]{2,3})\s*</small>'      # <small>DET</small>
    r'|\(([A-Z]{2,3})\)'                            # (DET)
    r'|</a>\s*([A-Z]{2,3})\b')                      # </a> DET
_NUM_TD_RE = re.compile(r'<td[^>]*>\s*([\d,\.]+)\s*</td>', re.IGNORECASE)


def available() -> bool:
    return requests is not None


def _get(url: str, timeout: float = 10.0) -> Optional[str]:
    if requests is None:
        return None
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception:
        return None


def _num(s: str) -> float:
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return 0.0


def _parse_projection_table(html: str, pos: str) -> list[dict]:
    """Return [{name, team, stats}] parsed from one position page."""
    cols = _COLS.get(pos)
    if not cols:
        return []
    players: list[dict] = []
    for label_html, rest_html in _ROW_RE.findall(html):
        nm = _NAME_RE.search(label_html)
        if not nm:
            continue
        name = re.sub(r"<[^>]+>", "", nm.group(1)).strip()
        tm = _TEAM_RE.search(label_html)
        team = ""
        if tm:
            team = tm.group(1) or tm.group(2) or tm.group(3) or ""
        nums = [_num(x) for x in _NUM_TD_RE.findall(rest_html)]
        if not nums:
            continue
        stats: dict[str, float] = {}
        for i, key in enumerate(cols):
            if i < len(nums) and key in _STAT_KEYS:
                stats[key] = nums[i]
        if name and stats:
            players.append({"name": name, "team": team, "position": pos.upper(),
                            "stats": stats})
    return players


def _parse_adp(html: str) -> dict[str, float]:
    """Map player name -> ADP (rank) from an ADP page."""
    out: dict[str, float] = {}
    rank = 0
    for label_html, rest_html in _ROW_RE.findall(html):
        nm = _NAME_RE.search(label_html)
        if not nm:
            continue
        name = re.sub(r"<[^>]+>", "", nm.group(1)).strip()
        nums = _NUM_TD_RE.findall(rest_html)
        # ADP is typically the last numeric column; fall back to row order
        adp = _num(nums[-1]) if nums else 0.0
        rank += 1
        out[name] = adp if adp > 0 else float(rank)
    return out


def _norm(name: str) -> str:
    """Normalize a player name for cross-source matching (strip Jr./III/punct)."""
    n = name.lower()
    for suf in (" jr.", " jr", " sr.", " sr", " iii", " ii", " iv", " v"):
        if n.endswith(suf):
            n = n[: -len(suf)]
    return re.sub(r"[^a-z0-9]", "", n)


def fetch_ffc_adp(teams: int = 12, year: int = 2026) -> dict[str, dict]:
    """Fetch real ADP (+ bye) per format from FantasyFootballCalculator's JSON
    API. Returns {norm_name: {"adp": {fmt: val}, "bye": int, "team": str}}.
    Empty on failure."""
    if requests is None:
        return {}
    out: dict[str, dict] = {}
    for our_fmt, ffc_fmt in _FFC_FMT.items():
        url = _FFC.format(fmt=ffc_fmt, teams=teams, year=year)
        try:
            r = requests.get(url, headers={"User-Agent": _UA}, timeout=10)
            r.raise_for_status()
            data = r.json()
        except Exception:
            continue
        for p in data.get("players", []):
            key = _norm(p.get("name", ""))
            if not key:
                continue
            e = out.setdefault(key, {"adp": {}, "bye": p.get("bye"),
                                     "team": p.get("team", "")})
            e["adp"][our_fmt] = p.get("adp")
            if p.get("bye"):
                e["bye"] = p["bye"]
    return out


def fetch_ffc_full(teams: int = 12, year: int = 2026) -> list[dict]:
    """Full FFC pool spine: every drafted player with ADP(all formats), bye,
    team, position. ~190 players. Empty on failure."""
    if requests is None:
        return []
    merged: dict[str, dict] = {}
    for our_fmt, ffc_fmt in _FFC_FMT.items():
        url = _FFC.format(fmt=ffc_fmt, teams=teams, year=year)
        try:
            r = requests.get(url, headers={"User-Agent": _UA}, timeout=10)
            r.raise_for_status()
            data = r.json()
        except Exception:
            continue
        for p in data.get("players", []):
            pos = (p.get("position") or "").upper()
            if pos in ("PK", "K"):
                pos = "K"
            if pos in ("DEF", "DST"):
                pos = "DST"
            key = _norm(p.get("name", ""))
            if not key:
                continue
            e = merged.setdefault(key, {
                "name": p.get("name", ""), "position": pos,
                "team": p.get("team", ""), "bye": p.get("bye"),
                "adp": {}, "stats": {},
            })
            e["adp"][our_fmt] = p.get("adp")
    return list(merged.values())


def _proxy_stats(position: str, adp: float, team: str = "") -> dict:
    """When no scraped projection exists, derive a rough stat line from ADP so
    the player still ranks (better than a zero). Monotonic decay by ADP."""
    # crude points target by ADP band, then back out a plausible stat line
    base = max(40.0, 320.0 - 1.4 * (adp or 180))
    if position == "RB":
        return {"rush_yd": base * 3.2, "rush_td": base * 0.03,
                "rec": base * 0.12, "rec_yd": base * 0.9, "rec_td": base * 0.01}
    if position == "WR":
        return {"rec": base * 0.30, "rec_yd": base * 4.2, "rec_td": base * 0.03}
    if position == "TE":
        return {"rec": base * 0.28, "rec_yd": base * 3.4, "rec_td": base * 0.03}
    if position == "QB":
        return {"pass_yd": base * 13, "pass_td": base * 0.09, "int": base * 0.03,
                "rush_yd": base * 1.2, "rush_td": base * 0.02}
    if position == "K":
        # earlier-ADP kickers (better offenses/legs) => more FGs & XPs (season)
        strength = max(0.4, min(1.4, (200 - (adp or 180)) / 110.0))
        # OPPORTUNITY: FG-friendly (conservative, stalling) offenses feed the K
        try:
            import advanced_metrics as _ADV
            opp, _ = _ADV.kicker_context(team)
        except Exception:
            opp = 1.0
        s = strength * opp
        return {"fg_0_39": 16 * s, "fg_40_49": 8 * s,
                "fg_50": 4 * s, "fg_miss": 4 / max(strength, 0.5),
                "xp_made": 34 * s, "xp_miss": 1.0}
    if position == "DST":
        # elite (early-ADP) defenses => more sacks/TOs/TDs + a good PA tier.
        strength = max(0.4, min(1.5, (200 - (adp or 190)) / 90.0))
        try:
            import advanced_metrics as _ADV
            dctx, _ = _ADV.dst_context(team)
        except Exception:
            dctx = 1.0
        s = strength * dctx
        pa_pts = 3.0 * 17 * s - 1.0 * 17 * (1 - s)   # season PA pts
        return {"dst_sack": 40 * s, "dst_int": 14 * s,
                "dst_fum_rec": 8 * s, "dst_td": 3 * s,
                "dst_safety": 0.5 * s, "dst_block": 0.6 * s,
                "dst_pa_pts": max(-20.0, pa_pts)}
    return {}


def fetch_pool(teams: int = 12, year: int = 2026) -> list[dict]:
    """Full-depth pool: FFC spine (~190 players, real ADP/bye) with FantasyPros
    consensus projections attached by name; ADP-proxy stat lines fill the rest
    so every drafted player ranks. Empty on failure -> seed fallback."""
    if requests is None:
        return []

    spine = fetch_ffc_full(teams=teams, year=year)
    if not spine:
        return []

    # ---- multi-source consensus ADP: FFC (already on spine) + Sleeper ----
    # FFC per-player adp dict is on the spine; add Sleeper as a second source.
    ffc_src = {_norm(p["name"]): {"adp": p.get("adp", {})} for p in spine}
    sleeper_src = fetch_sleeper_adp(year=year)
    cons = {}
    for _fmt in ("ppr", "half", "std"):
        c = consensus_adp([ffc_src, sleeper_src], fmt=_fmt)
        for key, d in c.items():
            cons.setdefault(key, {"adp": {}, "n": d["n"]})
            cons[key]["adp"][_fmt] = d["adp"]
            cons[key]["n"] = max(cons[key].get("n", 0), d["n"])

    # scrape FantasyPros projections and index by normalized name
    proj_by_name: dict[str, dict] = {}
    for pos in ("qb", "rb", "wr", "te"):
        html = _get(_PROJ.format(pos=pos))
        if html:
            for pl in _parse_projection_table(html, pos):
                proj_by_name[_norm(pl["name"])] = pl

    for p in spine:
        key = _norm(p["name"])
        # blend consensus ADP over FFC's own (consensus wins when >1 source)
        cd = cons.get(key)
        if cd and cd["adp"]:
            merged_adp = dict(p.get("adp") or {})
            merged_adp.update({k: v for k, v in cd["adp"].items() if v})
            p["adp"] = merged_adp
            p["adp_sources"] = cd.get("n", 1)
        else:
            p["adp_sources"] = 1
        proj = proj_by_name.get(key)
        if proj and proj.get("stats"):
            p["stats"] = proj["stats"]
            if not p.get("team") and proj.get("team"):
                p["team"] = proj["team"]
        else:
            adp_half = (p.get("adp") or {}).get("half") or \
                       (p.get("adp") or {}).get("ppr") or 180
            p["stats"] = _proxy_stats(p["position"], adp_half, p.get("team", ""))
    return spine



# --------------------------------------------------------------------------- Sleeper ADP
_SLEEPER_ADP = "https://api.sleeper.app/v1/players/nfl/adp/{fmt}?season={year}"
_SLEEPER_FMT = {"ppr": "ppr", "half": "half_ppr", "std": "std"}


def fetch_sleeper_adp(year: int = 2026) -> dict[str, dict]:
    """Best-effort Sleeper ADP per format -> {norm_name: {fmt: adp}}. Sleeper's
    ADP endpoint shape varies; we tolerate a few known shapes and return {} on
    any failure (it's an ADDITIVE consensus source, never required)."""
    if requests is None:
        return {}
    out: dict[str, dict] = {}
    for our_fmt, sl_fmt in _SLEEPER_FMT.items():
        url = _SLEEPER_ADP.format(fmt=sl_fmt, year=year)
        try:
            r = requests.get(url, headers={"User-Agent": _UA}, timeout=8)
            r.raise_for_status()
            data = r.json()
        except Exception:
            continue
        rows = data if isinstance(data, list) else data.get("players", []) \
            if isinstance(data, dict) else []
        for row in rows or []:
            nm = row.get("full_name") or row.get("name") or ""
            adp = row.get("adp") or row.get("average_pick") or row.get("adp_overall")
            key = _norm(nm)
            if key and adp:
                out.setdefault(key, {})[our_fmt] = float(adp)
    return out


def consensus_adp(sources: list[dict[str, dict]], fmt: str = "half") -> dict[str, dict]:
    """Blend multiple {norm_name: {fmt: adp}} maps into one consensus ADP.
    Returns {norm_name: {"adp": mean, "n": source_count, "spread": max-min}}.
    Averages the per-source ADP for `fmt` (falling back to any format a source
    has for that player). More sources agreeing = higher-confidence rank."""
    agg: dict[str, list[float]] = {}
    for src in sources:
        if not src:
            continue
        for key, val in src.items():
            a = None
            if isinstance(val, dict):
                a = val.get(fmt) or val.get("ppr") or val.get("half") or val.get("std")
                # val may itself be {"adp": {...}} (FFC full shape)
                if a is None and isinstance(val.get("adp"), dict):
                    ad = val["adp"]
                    a = ad.get(fmt) or ad.get("ppr") or ad.get("half") or ad.get("std")
            elif isinstance(val, (int, float)):
                a = float(val)
            if a and a > 0:
                agg.setdefault(key, []).append(float(a))
    out: dict[str, dict] = {}
    for key, vals in agg.items():
        out[key] = {"adp": round(sum(vals) / len(vals), 1), "n": len(vals),
                    "spread": round(max(vals) - min(vals), 1)}
    return out
