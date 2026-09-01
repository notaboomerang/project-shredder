"""
Contextual draft insights — the smarts that pop up ONLY when the situation calls
for them.

Instead of burying Stack Lab / Prophecy / Roster Lab / Dark Horse in tabs you
have to go hunting for, this module inspects the live board + your roster and
returns just the insights that actually apply right now. The UI renders each as
a compact card above the board, so the advice comes to you at the moment it
matters (e.g. "you roster Josh Allen and his WR is available — consider the
stack over the top pick") and stays silent otherwise.

Every insight is a plain dict so the UI can render it uniformly:
  {
    "kind":    machine tag ("stack", "cliff", "snipe", "combo", "dark_horse")
    "icon":    emoji
    "title":   short headline
    "body":    one or two sentences of plain-English reasoning
    "player":  the player this insight suggests acting on (or None)
    "position":the player's position (for the draft button) (or None)
    "priority":lower = more urgent (sorts the cards)
  }

Pure functions of (pool, cfg, roster, drafted, current_overall, recs, opponents).
Nothing here mutates state; the UI decides what to do with the suggestions.
"""
from __future__ import annotations

from typing import Optional

import roster_lab as RLAB
import wheel_play as WP
import dark_horse as DH
import prophecy as PROPH


def _rank_of(recs, name) -> Optional[int]:
    """Overall board rank (1-indexed) of a player in the current recs list."""
    for i, r in enumerate(recs, 1):
        if r.name == name:
            return i
    return None


def stack_insights(pool, cfg, my_roster, drafted, recs, name_to_raw,
                   max_rank: int = 30) -> list[dict]:
    """You roster a QB (or a pass-catcher) and their same-team partner is
    AVAILABLE and reasonably ranked — worth considering for the correlation.

    Only fires when the partner is inside `max_rank` of the board so we never
    suggest reaching for a stack that isn't close to value.
    """
    out: list[dict] = []
    my_qb_teams: dict[str, str] = {}       # team -> your QB's name
    my_pc_teams: dict[str, str] = {}       # team -> a pass-catcher you roster
    for nm, pos in my_roster:
        raw = name_to_raw.get(nm)
        if not raw or not raw.team:
            continue
        if pos == "QB":
            my_qb_teams[raw.team] = nm
        elif pos in ("WR", "TE"):
            my_pc_teams.setdefault(raw.team, nm)

    if not my_qb_teams and not my_pc_teams:
        return out

    seen_players: set[str] = set()
    for i, r in enumerate(recs, 1):
        if r.name in drafted or r.name in seen_players:
            continue
        rank = i
        # available pass-catcher for a QB you roster
        if r.position in ("WR", "TE") and r.team in my_qb_teams and rank <= max_rank:
            qb = my_qb_teams[r.team]
            out.append({
                "kind": "stack", "icon": "🔗",
                "title": f"Stack available: {r.name} with your QB {qb}",
                "body": (f"You roster {qb} ({r.team}). His {r.position} "
                         f"{r.name} is on the board at board-rank #{rank} "
                         f"(ADP {r.adp if r.adp else '—'}). Pairing them "
                         f"correlates your scoring — when {qb} throws a TD, your "
                         f"{r.position} often catches it. Worth taking over the "
                         f"top pick if the value's close."),
                "player": r.name, "position": r.position, "priority": 12 + rank,
            })
            seen_players.add(r.name)
        # available QB for a pass-catcher you roster
        elif r.position == "QB" and r.team in my_pc_teams and rank <= max_rank:
            pc = my_pc_teams[r.team]
            out.append({
                "kind": "stack", "icon": "🔗",
                "title": f"Stack available: QB {r.name} with your {pc}",
                "body": (f"You roster {pc} ({r.team}). Their QB {r.name} is on "
                         f"the board at board-rank #{rank}. Rostering the QB "
                         f"throwing to your pass-catcher stacks correlated "
                         f"upside."),
                "player": r.name, "position": r.position, "priority": 16 + rank,
            })
            seen_players.add(r.name)
    return out[:2]   # at most two stack nudges so it never floods


def cliff_insights(pool, drafted, my_roster, scoring_key) -> list[dict]:
    """A position you still NEED is about to fall off a tier cliff."""
    out: list[dict] = []
    try:
        alarms = RLAB.tier_cliff(pool, set(drafted), list(my_roster), scoring_key)
    except Exception:  # noqa: BLE001
        return out
    for a in alarms:
        if a.urgency == "now":
            out.append({
                "kind": "cliff", "icon": "🚨",
                "title": f"{a.position} tier cliff is HERE",
                "body": (f"Only {a.remaining_tier} startable {a.position} left and "
                         f"you still need {a.need}. After these, there's a real "
                         f"drop-off — prioritize {a.position} now before the tier "
                         f"empties."),
                "player": None, "position": a.position, "priority": 1,
            })
        elif a.urgency == "soon":
            out.append({
                "kind": "cliff", "icon": "⚠️",
                "title": f"{a.position} tier thinning",
                "body": (f"{a.remaining_tier} startable {a.position} remain (you "
                         f"need {a.need}). The cliff is a round or two away — "
                         f"don't wait too long."),
                "player": None, "position": a.position, "priority": 6,
            })
    return out[:2]


def snipe_insights(pool, cfg, drafted, current_overall, opponents,
                   scoring_key, min_conf: float = 0.45,
                   loyalty_by_slot=None) -> list[dict]:
    """An opponent is predicted to grab a player you have an EARLIER pick to
    take first — grab-now-to-deny. When `loyalty_by_slot` is supplied (learned
    from past drafts), prophecy predicts the ACTUAL player each manager keeps
    taking, not just the position."""
    out: list[dict] = []
    if opponents is None:
        return out
    try:
        preds = PROPH.predict_board(pool, cfg, set(drafted), int(current_overall),
                                    opponents=opponents, scoring_key=scoring_key,
                                    horizon=int(cfg.teams) * 2,
                                    loyalty_by_slot=loyalty_by_slot or {})
        snipes = PROPH.find_snipes(preds, cfg, min_conf=min_conf)
    except Exception:  # noqa: BLE001
        return out
    for s in snipes[:2]:
        out.append({
            "kind": "snipe", "icon": "🎯",
            "title": f"Snipe: grab {s.player} before they do",
            "body": (f"A rival is likely to take {s.player} ({s.position}) around "
                     f"pick #{s.their_pick_overall} "
                     f"({int(s.confidence*100)}% confidence). You pick at "
                     f"#{s.your_pick_overall} — take him first to deny them."),
            "player": s.player, "position": s.position, "priority": 8,
        })
    return out


def combo_insights(pool, my_roster, drafted, recs, scoring_key,
                   name_to_raw) -> list[dict]:
    """A same-team QB+RB scoring-machine pair (40+ combined TDs) where at least
    one half is available — and ideally you already own the other half."""
    out: list[dict] = []
    try:
        combos = WP.td_combos(pool, scoring_key, min_tds=40.0)
    except Exception:  # noqa: BLE001
        return out
    my_names = {n for n, _ in my_roster}
    for c in combos:
        qb_gone = c["qb"] in drafted
        rb_gone = c["rb"] in drafted
        qb_mine = c["qb"] in my_names
        rb_mine = c["rb"] in my_names
        # you own one half, the other is available -> complete the package
        if qb_mine and not rb_gone and not rb_mine:
            out.append({
                "kind": "combo", "icon": "➕",
                "title": f"Complete your TD stack: {c['rb']}",
                "body": (f"You roster {c['qb']} ({c['team']}). His RB {c['rb']} "
                         f"is available — together they project "
                         f"{c['combined']:.0f} combined TDs. Owning both "
                         f"concentrates a scoring-machine backfield/offense."),
                "player": c["rb"], "position": "RB", "priority": 14,
            })
        elif rb_mine and not qb_gone and not qb_mine:
            out.append({
                "kind": "combo", "icon": "➕",
                "title": f"Complete your TD stack: {c['qb']}",
                "body": (f"You roster {c['rb']} ({c['team']}). Their QB "
                         f"{c['qb']} is available — {c['combined']:.0f} combined "
                         f"projected TDs as a package."),
                "player": c["qb"], "position": "QB", "priority": 18,
            })
    return out[:1]   # one combo nudge at a time


def dark_horse_insight(pool, cfg, my_roster, drafted, current_overall,
                       scoring_key) -> list[dict]:
    """Late-draft lottery ticket — only when K+DST are filled and you're near
    your last pick (gated inside dark_horse.recommend_if_right)."""
    try:
        dh = DH.recommend_if_right(pool, cfg, list(my_roster), set(drafted),
                                   int(current_overall), scoring_key)
    except Exception:  # noqa: BLE001
        return []
    if not dh:
        return []
    return [{
        "kind": "dark_horse", "icon": "🐴",
        "title": f"Dark horse: {dh.name}",
        "body": dh.thesis,
        "player": dh.name, "position": dh.position, "priority": 20,
    }]


def gather(pool, cfg, my_roster, drafted, current_overall, recs, opponents,
           scoring_key, name_to_raw, loyalty_by_slot=None) -> list[dict]:
    """Run every situational check and return the insights that apply now,
    most-urgent first. Silent (empty list) when nothing special is happening."""
    insights: list[dict] = []
    insights += cliff_insights(pool, drafted, my_roster, scoring_key)
    insights += stack_insights(pool, cfg, my_roster, drafted, recs, name_to_raw)
    insights += combo_insights(pool, my_roster, drafted, recs, scoring_key,
                               name_to_raw)
    insights += snipe_insights(pool, cfg, drafted, current_overall, opponents,
                               scoring_key, loyalty_by_slot=loyalty_by_slot)
    insights += dark_horse_insight(pool, cfg, my_roster, drafted,
                                   current_overall, scoring_key)
    insights.sort(key=lambda d: d.get("priority", 50))
    # de-dupe by (kind, player) so the same nudge never doubles up
    seen = set()
    uniq = []
    for ins in insights:
        key = (ins["kind"], ins.get("player"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(ins)
    return uniq
