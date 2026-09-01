"""
PROJECT SHREDDER — draft-day cheat sheet PDF generator.

For each of your ESPN leagues, this:
  1. pulls past-draft opponent DNA (learn_dna_by_manager) mapped to 2026 seats,
  2. simulates the DNA-aware room forward to each of YOUR picks,
  3. writes a styled one-league PDF with the PROJECT SHREDDER headline:
       - your snake pick numbers + the gap structure
       - an opponent readout (each seat's tendencies + loyalty picks)
       - a round-by-round plan (best-available at each of your picks)

Run:  py -3.13 make_cheatsheets.py
Output: assets/cheatsheets/shredder_<league>.pdf  (+ a combined all-leagues PDF)

Notes:
  - Uses the SAME engine the live app uses, so the reads match the board.
  - The room sim auto-pilots your seat by best-available value; the 2nd-elite-TE
    quirk is annotated in the plan (your live board's surplus logic prevents it).
"""
from __future__ import annotations

import os
import re

import secrets_store as SEC
import league_history as LH
import engine as E
import projections as P
import edge_engine as X
import opponents as O
import mock_draft as MOCK
import espn_client as EC
import wheel_play as WP

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, HRFlowable)
from reportlab.lib.enums import TA_LEFT

# --- your leagues (id, display name, your seat, team count) ---
# Seats verified against the 2026 slot->owner map (current_slot_to_owner):
#   Not Yo Momma's = kennyfreakingzemaitis (11)
#   Mascot Vac 2   = bszema07 (9)   <- you draft under a different handle here
#   F3             = kennyfreakingzemaitis (16)
#   the big zen    = kennyfreakingzemaitis (10)
LEAGUES = [
    (77269, "Not Yo Momma's League", 11, 12),
    (630798, "Mascot Vacationers 2", 9, 12),
    (46110526, "F3", 16, 16),
    (2044275115, "the big zen", 10, 12),
]
SEASONS = [2021, 2022, 2023, 2024, 2025]

# --- pure black & white palette (print zine look) ---
INK = colors.black
PAPER = colors.white
DIM = colors.HexColor("#555555")          # only shade of gray, for fine print
LINE = colors.HexColor("#000000")
HAIR = colors.HexColor("#bbbbbb")         # hairline rules between rows
PANEL = colors.HexColor("#ededed")        # faint header fill (still B&W-safe)
ACCENT = colors.black                     # tags print black (bold, not colored)

_ASSETS = os.path.join(os.path.dirname(__file__), "assets")
_OUT = os.path.join(_ASSETS, "cheatsheets")
# the processed marks (white-on-transparent for the black masthead, black for body)
_MARK_WHITE = os.path.join(_ASSETS, "shredder_mark_white.png")
_MARK_BLACK = os.path.join(_ASSETS, "shredder_mark_black.png")
_ICON = os.path.join(_ASSETS, "shredder_icon_128.png")
_ICON_SRC = os.path.join(_ASSETS, "shredder_icon.png")   # original 512px art


def _styles():
    ss = getSampleStyleSheet()
    out = {}
    out["mast"] = ParagraphStyle("mast", parent=ss["Title"], fontName="Helvetica-Bold",
                                 fontSize=33, textColor=PAPER, spaceAfter=0, leading=33,
                                 alignment=TA_LEFT)
    out["tag"] = ParagraphStyle("tag", parent=ss["Normal"], fontName="Courier-Bold",
                                fontSize=8, textColor=PAPER, spaceAfter=0, leading=11)
    out["league"] = ParagraphStyle("league", parent=ss["Heading1"],
                                   fontName="Helvetica-Bold", fontSize=15,
                                   textColor=INK, spaceBefore=8, spaceAfter=1)
    out["sub"] = ParagraphStyle("sub", parent=ss["Normal"], fontSize=8.5,
                                fontName="Courier", textColor=DIM, spaceAfter=4)
    out["h2"] = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                               fontSize=10.5, textColor=PAPER, spaceBefore=8, spaceAfter=3,
                               leading=13)
    out["body"] = ParagraphStyle("body", parent=ss["Normal"], fontSize=8,
                                 textColor=INK, leading=10.5, spaceAfter=2, alignment=TA_LEFT)
    out["cell"] = ParagraphStyle("cell", parent=ss["Normal"], fontSize=7.3,
                                 textColor=INK, leading=9)
    out["cellb"] = ParagraphStyle("cellb", parent=ss["Normal"], fontSize=7.3,
                                  fontName="Helvetica-Bold", textColor=INK, leading=9)
    out["cellw"] = ParagraphStyle("cellw", parent=ss["Normal"], fontSize=7.3,
                                  fontName="Helvetica-Bold", textColor=PAPER, leading=9)
    out["foot"] = ParagraphStyle("foot", parent=ss["Normal"], fontSize=6.6,
                                 fontName="Courier", textColor=DIM, leading=8.4)
    return out


def _section_head(styles, text):
    """A solid black section bar with white text — the zine header look."""
    t = Table([[Paragraph(text.upper(), styles["h2"])]], colWidths=[7.4 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), INK),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _load_common():
    s2, swid, src = SEC.auto_load()
    pool = P.load_players(prefer_live=True)
    return s2, swid, src, pool


def _resolve_my_seat(s2o, my_team_name_hint="Fantasy Football 2026"):
    """Best-effort: find which seat is mine by owner display name match."""
    for slot, (owner, name) in s2o.items():
        if my_team_name_hint and my_team_name_hint.lower() in str(name).lower():
            return slot
    return None


def _stack_intel(pool, scoring_key):
    """Build stack intel for a league's scoring:
      combos      : list of 40+ combined-TD QB/RB packages (from wheel_play)
      td_names    : {player_name: '+TD w/ <partner> (<combined> TD)'} tag per
                    player in a 40+ package
      qb_partner  : {qb_name: (top_pass_catcher_name, pos)} same-team stack
      pc_partner  : {pass_catcher_name: (qb_name)} reverse map
    Used to stamp each target and to print a Stack Board section.
    """
    combos = WP.td_combos(pool, scoring_key, min_tds=40.0)
    td_names = {}
    for c in combos:
        td_names[c["qb"]] = f"+TD w/ {c['rb'].split()[-1]} ({c['combined']:.0f} TD)"
        td_names[c["rb"]] = f"+TD w/ {c['qb'].split()[-1]} ({c['combined']:.0f} TD)"

    # same-team QB <-> top pass-catcher (WR/TE) by projected points for THIS format
    by_team_qb, by_team_pc = {}, {}
    for raw in pool:
        pts = E.project_points(raw.stats, E.Scoring.preset(scoring_key))
        if raw.position == "QB":
            by_team_qb.setdefault(raw.team, []).append((raw.name, pts))
        elif raw.position in ("WR", "TE"):
            by_team_pc.setdefault(raw.team, []).append((raw.name, raw.position, pts))
    qb_partner, pc_partner = {}, {}
    for team, qbs in by_team_qb.items():
        pcs = by_team_pc.get(team, [])
        if not qbs or not pcs:
            continue
        qb = max(qbs, key=lambda x: x[1])[0]
        pc = max(pcs, key=lambda x: x[2])
        qb_partner[qb] = (pc[0], pc[1])       # qb -> (pass-catcher, pos)
        pc_partner[pc[0]] = qb                 # pass-catcher -> qb
    return {"combos": combos, "td_names": td_names,
            "qb_partner": qb_partner, "pc_partner": pc_partner}


def _reachable_from_seat(adp, my_overalls, window=12):
    """Can I plausibly roster a player at `adp` from my seat? True if one of MY
    overall picks lands within `window` picks at/after his ADP (i.e. he'd still
    be on the board when I'm up, without a wild reach)."""
    if adp is None:
        return False
    for ov in my_overalls:
        if adp - 6 <= ov <= adp + window:
            return True
    return False


def _package_feasibility(combo, my_overalls):
    """For a QB/RB package, decide if the SEAT can realistically roster BOTH:
    each half must be reachable at one of your picks, and not require the same
    single pick. Returns (feasible_both, note)."""
    qa, ra = combo.get("qb_adp"), combo.get("rb_adp")
    qb_ok = _reachable_from_seat(qa, my_overalls, window=14)
    rb_ok = _reachable_from_seat(ra, my_overalls, window=14)
    if qb_ok and rb_ok:
        return True, "GRAB BOTH"
    if rb_ok and qa is not None and qa < min(my_overalls, default=999):
        return False, "RB yes / QB gone early"
    if qb_ok and not rb_ok:
        return False, "QB yes / RB goes before you"
    if rb_ok and not qb_ok:
        return False, "RB yes / QB reach"
    return False, "tough from your seat"


def _stack_tag(name, position, intel):
    """Short stack note for a single player, or '' if none."""
    parts = []
    if name in intel["td_names"]:
        parts.append(intel["td_names"][name])
    if position == "QB" and name in intel["qb_partner"]:
        pc, pos = intel["qb_partner"][name]
        parts.append(f"stack -> {pc.split()[-1]} ({pos})")
    elif position in ("WR", "TE") and name in intel["pc_partner"]:
        parts.append(f"stack -> {intel['pc_partner'][name].split()[-1]} (QB)")
    return " · ".join(parts)


def _league_data(lid, seat, teams, pool, s2, swid):
    """Return (cfg, opps, mgr, s2o, plan_rows). plan_rows = list of dicts per
    your pick: {rnd, overall, rb_gone, wr_gone, recs(list of top5), took}.
    Scoring/roster settings are pulled LIVE from ESPN so each league's format
    (Full PPR / Half / Standard, team count, lineup, bench) is exact."""
    # ---- pull the league's REAL settings from ESPN (format, scoring, roster) ----
    scoring_key = "half"
    scoring_fmt = "Half PPR"
    try:
        _cli = EC.EspnClient(int(lid), 2026, s2, swid)
        _prof = _cli.settings_profile() or {}
    except Exception as _ex:  # noqa: BLE001
        print(f"  ! settings fetch failed for {lid}: {_ex} — using half-PPR default")
        _prof = {}
    if _prof:
        teams = int(_prof.get("teams") or teams)
        scoring_fmt = _prof.get("scoring_format") or scoring_fmt
        _sc = _prof.get("scoring") or {}
        rp = _sc.get("reception", 0.5)
        scoring_key = "ppr" if rp >= 1.0 else ("half" if rp >= 0.5 else "std")
        scoring_obj = E.Scoring(**{k_: v_ for k_, v_ in _sc.items()
                                   if k_ in E.Scoring.__dataclass_fields__})
        starters = _prof.get("starters") or {"QB": 1, "RB": 2, "WR": 2, "TE": 1,
                                             "FLEX": 1, "DST": 1, "K": 1}
        bench = int(_prof.get("bench") or 7)
    else:
        scoring_obj = E.Scoring.preset("half")
        starters = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "DST": 1, "K": 1}
        bench = 7

    cfg = E.LeagueConfig(teams=teams, draft_slot=(seat or 1), rounds=16,
                         scoring=scoring_obj, starters=starters, bench=bench)
    cfg._scoring_fmt = scoring_fmt   # stash label for the PDF header
    cfg._scoring_key = scoring_key

    drafts = LH.pull_past_drafts(lid, SEASONS, s2, swid)
    if drafts:
        try:
            import manager_analysis as MA
            # pool across ALL your leagues so shared managers get the bigger read
            league_drafts = {lid: drafts}
            league_ctx = {lid: LH.season_contexts(lid, SEASONS, s2, swid)}
            _names_by_id = {l[0]: l[1] for l in LEAGUES}
            lg_names = {lid: _names_by_id.get(lid, str(lid))}
            for other_lid, other_name, *_ in LEAGUES:
                if other_lid == lid:
                    continue
                od = _cached_drafts(other_lid, s2, swid)
                if od:
                    league_drafts[other_lid] = od
                    league_ctx[other_lid] = LH.season_contexts(other_lid, SEASONS,
                                                               s2, swid)
                    lg_names[other_lid] = other_name
            mgr = MA.analyze_across_leagues(league_drafts, league_ctx,
                                            current_scoring_key=scoring_key,
                                            league_names=lg_names)
        except Exception as _mex:  # noqa: BLE001
            print(f"  ! deep analysis failed for {lid}: {_mex} — using basic learner")
            mgr = LH.learn_dna_by_manager(drafts)
    else:
        mgr = {}
    s2o = LH.current_slot_to_owner(lid, 2026, s2, swid)
    if seat is None:
        seat = _resolve_my_seat(s2o) or 1
        cfg.draft_slot = seat
    opps = O.LeagueOpponents.default(teams, seat)
    if mgr and s2o:
        LH.apply_manager_dna(opps, mgr, s2o)

    n2r = {p.name: p for p in pool}
    drafted, tr, pl = set(), {}, []
    mypicks = set(cfg.my_overall_picks())
    ov, total = 1, teams * 16
    plan = []
    while ov <= total and len(drafted) < len(pool):
        if ov in mypicks:
            mine = [(n, n2r[n].position) for n in tr.get(seat, [])]
            recs = X.recommend(pool, cfg, X.Roster(mine), drafted, ov, scoring_key,
                               top_n=5, opponents=opps)
            rbg = sum(1 for n in drafted if n2r.get(n) and n2r[n].position == "RB")
            wrg = sum(1 for n in drafted if n2r.get(n) and n2r[n].position == "WR")
            took = recs[0].name if recs else None
            plan.append({"rnd": (ov - 1) // teams + 1, "overall": ov,
                         "rb_gone": rbg, "wr_gone": wrg,
                         "recs": recs[:5], "took": took})
            if not recs:
                break
            drafted.add(recs[0].name)
            tr.setdefault(seat, []).append(recs[0].name)
            pl.append((ov, recs[0].name, recs[0].position, seat))
            ov += 1
        else:
            r = MOCK.bots_pick_until_me(pool, cfg, drafted, tr, ov,
                                        opponents=opps, pick_log=pl)
            ov = r["now_overall"]
        if ov > max(mypicks):
            break
    return cfg, opps, mgr, s2o, seat, plan


def _pick_math_row(cfg):
    picks = cfg.my_overall_picks()
    gaps = cfg.gaps_between_my_picks()
    parts = []
    for i, p in enumerate(picks[:9]):
        g = f" (+{gaps[i]})" if i < len(gaps) else ""
        parts.append(f"R{i+1}: #{p}{g}")
    return "   |   ".join(parts)


def _opp_cell(slot, name, d, seat, styles):
    """One compact opponent entry: 'S# NAME — tendencies (loyalty)'."""
    you = slot == seat
    label = f"{slot} {str(name)[:13]}"
    if you:
        label += " *YOU*"
    if not d:
        tend = "no history"
    else:
        tend = ", ".join(t for t in d.get("tendencies", []) if t != "ADP-robot") \
            or "ADP"
        # deep read: confidence grade + QB timing + loyalty
        cl = d.get("confidence_label")
        if cl:
            tend = f"[{cl}] {tend}"
        qf = d.get("qb_first_round")
        if qf is not None:
            tend += (f" · QB~r{qf:.0f} early" if qf <= 5 else f" · streams QB r{qf:.0f}")
        fav = d.get("favorite_players") or {}
        if fav:
            tend += " · loves " + ", ".join(
                (n.split()[-1] if " " in n else n) for n in list(fav)[:2])
        if d.get("cross_league"):
            tend += " · [pooled]"
    st = styles["cellb"] if you else styles["cell"]
    return Paragraph(f"<b>{label}</b><br/><font size=6.2>{tend}</font>", st)


def _opponent_table(mgr, s2o, seat, styles):
    # pack seats per row to stay single-page (3-wide for big leagues, else 2-wide)
    entries = []
    for slot in sorted(s2o):
        owner, name = s2o[slot]
        entries.append(_opp_cell(slot, name, mgr.get(owner), seat, styles))
    ncol = 3 if len(entries) > 12 else 2
    cw = 7.4 / ncol
    rows = []
    for i in range(0, len(entries), ncol):
        grp = entries[i:i + ncol]
        while len(grp) < ncol:
            grp.append(Paragraph("", styles["cell"]))
        rows.append(grp)
    t = Table(rows, colWidths=[cw * inch] * ncol)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, HAIR),
        ("LINEAFTER", (0, 0), (0, -1), 0.3, HAIR),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _plan_table(plan, styles, intel):
    rows = [[Paragraph("<b>RD</b>", styles["cellw"]),
             Paragraph("<b>PK</b>", styles["cellw"]),
             Paragraph("<b>BOARD</b>", styles["cellw"]),
             Paragraph("<b>BEST AVAILABLE  +  STACK INTEL</b>", styles["cellw"])]]
    for row in plan:
        if row["rnd"] > 8:            # rounds 1-8 fit the 2-page sheet cleanly
            continue
        targets = []
        for r in row["recs"][:3]:
            adp = f"{r.adp:.0f}" if r.adp else "—"
            base = f"<b>{r.position}{r.pos_rank}</b> {r.name} (ADP {adp})"
            stag = _stack_tag(r.name, r.position, intel)
            if stag:
                base += f'  <b>[{stag}]</b>'      # bold, prints clean in B&W
            targets.append(base)
        rows.append([
            Paragraph(f"<b>R{row['rnd']}</b>", styles["cellb"]),
            Paragraph(f"{row['overall']}", styles["cell"]),
            Paragraph(f"{row['rb_gone']}RB/{row['wr_gone']}WR", styles["cell"]),
            Paragraph("<br/>".join(targets), styles["cell"]),
        ])
    t = Table(rows, colWidths=[0.3 * inch, 0.4 * inch, 0.85 * inch, 5.85 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INK),      # black header row
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, HAIR),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _stack_board_table(intel, styles, my_overalls):
    """40+ combined-TD QB/RB packages with a FROM-YOUR-SEAT feasibility flag so
    you know which stacks you can realistically roster given your pick timing."""
    combos = intel["combos"]
    flow = []
    if combos:
        rows = [[Paragraph("<b>TEAM</b>", styles["cellw"]),
                 Paragraph("<b>QB + RB PACKAGE (40+ TD)</b>", styles["cellw"]),
                 Paragraph("<b>TD</b>", styles["cellw"]),
                 Paragraph("<b>ADPs (QB/RB)</b>", styles["cellw"]),
                 Paragraph("<b>FROM YOUR SEAT</b>", styles["cellw"])]]
        feas_flags = []
        for c in combos[:10]:
            qadp = f"{c['qb_adp']:.0f}" if c["qb_adp"] else "—"
            radp = f"{c['rb_adp']:.0f}" if c["rb_adp"] else "—"
            feasible, note = _package_feasibility(c, my_overalls)
            feas_flags.append(feasible)
            note_txt = (f"<b>{note}</b>" if feasible else note)
            rows.append([
                Paragraph(c["team"], styles["cellb"]),
                Paragraph(f"{c['qb']} + {c['rb']}", styles["cell"]),
                Paragraph(f"{c['combined']:.0f}", styles["cellb"]),
                Paragraph(f"{qadp} / {radp}", styles["cell"]),
                Paragraph(note_txt, styles["cell"]),
            ])
        t = Table(rows, colWidths=[0.5 * inch, 3.15 * inch, 0.35 * inch,
                                   1.3 * inch, 2.1 * inch])
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), INK),
            ("LINEBELOW", (0, 1), (-1, -1), 0.3, HAIR),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]
        # bold-box the feasible ("GRAB BOTH") rows so they pop in B&W
        for i, ok in enumerate(feas_flags, start=1):
            if ok:
                style.append(("BOX", (0, i), (-1, i), 1.1, INK))
        t.setStyle(TableStyle(style))
        flow.append(t)
    else:
        flow.append(Paragraph("No 40+ combined-TD packages in the current pool.",
                              styles["foot"]))
    return flow


def _masthead(styles):
    """A full-width solid-black masthead bar: the big inverted (white) Shredder
    mark on the left, PROJECT SHREDDER + tagline reversed out in white."""
    from reportlab.platypus import Image
    icon_cell = ""
    if os.path.exists(_ICON_SRC):
        icon_cell = Image(_ICON_SRC, width=1.0 * inch, height=1.0 * inch)
    title_stack = [
        Paragraph("PROJECT SHREDDER", styles["mast"]),
        Spacer(1, 2),
        Paragraph("// DRAFT-DAY CHEAT SHEET &nbsp;·&nbsp; VORP + EDGE ENGINE "
                  "&nbsp;·&nbsp; OPPONENT DNA &nbsp;·&nbsp; STACK INTEL", styles["tag"]),
    ]
    bar = Table([[icon_cell, title_stack]], colWidths=[1.15 * inch, 6.25 * inch])
    bar.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 10),
        ("LEFTPADDING", (1, 0), (1, 0), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 10),
    ]))
    return [bar, Spacer(1, 6)]


_DATA_CACHE = {}
_DRAFTS_CACHE = {}


def _cached_drafts(lid, s2, swid):
    """Pull-once cache of a league's past drafts (reused for cross-league pooling
    so we don't re-fetch each league 4x)."""
    if lid not in _DRAFTS_CACHE:
        try:
            _DRAFTS_CACHE[lid] = LH.pull_past_drafts(lid, SEASONS, s2, swid)
        except Exception:  # noqa: BLE001
            _DRAFTS_CACHE[lid] = []
    return _DRAFTS_CACHE[lid]


def _gather(lid, name, seat, teams, pool, s2, swid):
    """Run the (expensive) sim + DNA + stack intel ONCE per league and cache it,
    so both the per-league and combined PDFs render from the same data."""
    if lid in _DATA_CACHE:
        return _DATA_CACHE[lid]
    cfg, opps, mgr, s2o, seat, plan = _league_data(lid, seat, teams, pool, s2, swid)
    scoring_key = getattr(cfg, "_scoring_key", "half")
    intel = _stack_intel(pool, scoring_key)
    data = {"cfg": cfg, "mgr": mgr, "s2o": s2o, "seat": seat, "plan": plan,
            "intel": intel, "name": name}
    _DATA_CACHE[lid] = data
    return data


def build_league_flow(lid, name, seat, teams, pool, s2, swid, styles):
    d = _gather(lid, name, seat, teams, pool, s2, swid)
    cfg, mgr, s2o, seat, plan, intel = (d["cfg"], d["mgr"], d["s2o"], d["seat"],
                                        d["plan"], d["intel"])
    my_overalls = cfg.my_overall_picks()
    flow = []
    _fmt = getattr(cfg, "_scoring_fmt", "Half PPR")

    # league title line + settings + pick math (compact, no wasted vertical space)
    flow.append(Paragraph(name.upper(), styles["league"]))
    dna_live = bool(mgr and s2o)
    flow.append(Paragraph(
        f"{cfg.teams}-TEAM · {_fmt.upper()} · {cfg.bench}-MAN BENCH · YOUR SEAT {seat}"
        + ("   ·   [DNA ACTIVE]" if dna_live else "   ·   [DNA: ADP-BASED]"),
        styles["sub"]))
    flow.append(Paragraph("<b>YOUR PICKS:</b> " + _pick_math_row(cfg), styles["body"]))
    flow.append(Spacer(1, 7))

    # ---------- PAGE 1 : the room + the plan ----------
    flow.append(_section_head(styles, "The room — opponent DNA by seat"))
    flow.append(Spacer(1, 3))
    flow.append(_opponent_table(mgr, s2o, seat, styles))
    # cross-league notes: shared managers who draft differently in your leagues
    _splits = []
    for _sl in sorted(s2o):
        _o = s2o[_sl][0] if isinstance(s2o[_sl], (list, tuple)) else s2o[_sl]
        _d = mgr.get(_o)
        if _d and _d.get("split_note"):
            _who = _d.get("manager_name", f"seat {_sl}")
            _splits.append(f"<b>{_who}</b> — {_d['split_note']}")
    if _splits:
        flow.append(Spacer(1, 3))
        flow.append(Paragraph("<b>CROSS-LEAGUE NOTES</b> (shared managers who draft "
                              "differently in your leagues — discount their read here "
                              "accordingly):", styles["foot"]))
        for _s in _splits[:3]:
            flow.append(Paragraph("- " + _s, styles["foot"]))
    flow.append(Spacer(1, 6))

    flow.append(_section_head(styles, "Round-by-round plan (DNA-simmed room)"))
    flow.append(Spacer(1, 3))
    flow.append(_plan_table(plan, styles, intel))
    flow.append(Spacer(1, 3))
    flow.append(Paragraph("[ ] tags: <b>+TD</b> = part of a 40+ combined-TD QB/RB "
                          "package · <b>stack-&gt;</b> = same-team QB/pass-catcher "
                          "correlation. Rounds 1-9 shown.", styles["foot"]))

    # ---------- PAGE 2 : correlation + playbook ----------
    from reportlab.platypus import PageBreak
    flow.append(PageBreak())
    flow.append(Paragraph(f"{name.upper()} — STACK & CORRELATION BOARD",
                          styles["league"]))
    flow.append(Paragraph(f"{cfg.teams}-TEAM · {_fmt.upper()} · YOUR SEAT {seat}  "
                          "·  page 2 of 2", styles["sub"]))
    flow.append(Spacer(1, 6))

    flow.append(_section_head(styles, "40+ combined-TD packages · roster BOTH halves"))
    flow.append(Spacer(1, 3))
    flow.append(Paragraph("Rostering both halves concentrates a scoring-machine "
                          "offense (correlated TDs). <b>Boxed rows are reachable from "
                          "YOUR seat</b> — the 'GRAB BOTH' calls are your realistic "
                          "double-dips given your pick timing.", styles["foot"]))
    flow.append(Spacer(1, 4))
    flow.extend(_stack_board_table(intel, styles, my_overalls))
    flow.append(Spacer(1, 10))

    qbp = intel["qb_partner"]
    if qbp:
        flow.append(_section_head(styles, "QB / pass-catcher stacks (same team)"))
        flow.append(Spacer(1, 3))
        rows = []
        items = list(qbp.items())
        for j in range(0, len(items[:12]), 2):
            cells = []
            for qb, (pc, pos) in items[j:j + 2]:
                cells.append(Paragraph(f"<b>{qb}</b> + {pc} <font size=6.4>"
                                       f"({pos})</font>", styles["cell"]))
            while len(cells) < 2:
                cells.append(Paragraph("", styles["cell"]))
            rows.append(cells)
        qt = Table(rows, colWidths=[3.7 * inch, 3.7 * inch])
        qt.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, HAIR),
            ("LINEAFTER", (0, 0), (0, -1), 0.3, HAIR),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        flow.append(qt)
        flow.append(Spacer(1, 12))

    # the playbook, boxed
    play = Table([[Paragraph(
        "<b>THE PLAYBOOK</b><br/><br/>"
        "<b>1. Win the turn with scarce assets.</b> Take elite RB before the run and "
        "elite TE before the cliff — the long snake gaps mean you can't get that tier "
        "back.<br/>"
        "<b>2. Cash the QB slide.</b> Your rooms draft QB late, so a top-5 QB routinely "
        "falls 20-40 picks past ADP right to your seat. Let it come to you.<br/>"
        "<b>3. Break ties with stacks.</b> When two targets are close, take the one "
        "with a <b>[stack]</b> tag — correlated ceiling wins leagues. The boxed "
        "'GRAB BOTH' packages above are the ones you can actually pull off.<br/>"
        "<b>4. Trust the structure, not the names.</b> ADP shifts before kickoff — "
        "re-run the sheet the morning of to refresh.", styles["body"])]],
        colWidths=[7.4 * inch])
    play.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.4, INK),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    flow.append(play)
    flow.append(Spacer(1, 6))
    flow.append(Paragraph("Sim caveat: the plan auto-pilots your seat by best value "
                          "and may show a 2nd elite TE — your live board's roster-"
                          "surplus logic won't, so treat any duplicate-position slot "
                          "as 'best RB/WR'.", styles["foot"]))
    return flow


def main():
    os.makedirs(_OUT, exist_ok=True)
    styles = _styles()
    s2, swid, src, pool = _load_common()
    print(f"cookies: {src} · pool: {len(pool)} players")
    if not (s2 and swid):
        print("No ESPN cookies — can't pull DNA. Aborting.")
        return

    from reportlab.platypus import PageBreak
    MARGIN = dict(topMargin=0.42 * inch, bottomMargin=0.42 * inch,
                  leftMargin=0.55 * inch, rightMargin=0.55 * inch)
    combined = []
    for i, (lid, name, seat, teams) in enumerate(LEAGUES):
        print(f"building {name}…")
        try:
            flow = build_league_flow(lid, name, seat, teams, pool, s2, swid, styles)
        except Exception as ex:  # noqa: BLE001
            print(f"  skipped {name}: {ex}")
            continue
        # per-league SINGLE-PAGE PDF (its own masthead)
        safe = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        path = os.path.join(_OUT, f"shredder_{safe}.pdf")
        SimpleDocTemplate(path, pagesize=letter, title=f"Project Shredder — {name}",
                          **MARGIN).build(_masthead(styles) + flow)
        print(f"  -> {path}")
        # combined: rebuild the flow (flowables are consumed by build) so each
        # league gets its own masthead + page.
        if combined:
            combined.append(PageBreak())
        combined.extend(_masthead(styles))
        combined.extend(build_league_flow(lid, name, seat, teams, pool, s2, swid, styles))

    if combined:
        allpath = os.path.join(_OUT, "shredder_all_leagues.pdf")
        SimpleDocTemplate(allpath, pagesize=letter,
                          title="Project Shredder — All Leagues",
                          **MARGIN).build(combined)
        print(f"combined -> {allpath}")


if __name__ == "__main__":
    main()
