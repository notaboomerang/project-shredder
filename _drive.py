import traceback
lines = []
try:
    import projections as P, engine as E, edge_engine as X, opponents as O
    import mock_draft as MOCK, draft_queue as DQ, roster_lab as RL, situational as SIT
    pool = P.load_players(prefer_live=True)
    cfg = E.LeagueConfig.monday(); sk = "half"
    opps = O.LeagueOpponents.default(cfg.teams, cfg.draft_slot)
    drafted = set(); team_rosters = {}; my_roster = []; pick_log = []
    overall = 1
    for target_pick in cfg.my_overall_picks()[:8]:
        MOCK.bots_pick_until_me(pool, cfg, drafted, team_rosters, overall, opponents=opps, pick_log=pick_log)
        overall = target_pick
        recs = X.recommend(pool, cfg, X.Roster(players=list(my_roster)), set(drafted),
                           current_overall=overall, scoring_key=sk, top_n=5, opponents=opps)
        if not recs: break
        top = recs[0]; rnd = ((target_pick - 1) // cfg.teams) + 1
        lines.append("=== R%d | your pick #%d ===" % (rnd, target_pick))
        lines.append("  PICK: %s (%s-%s)  composite %s | VORP %s | %d%% survives"
                     % (top.name, top.position, top.team, top.composite, top.vorp, int((top.survival or 0)*100)))
        lines.append("    why: " + " | ".join(top.badges[:4]))
        q = DQ.build_queue(pool, cfg, set(drafted), list(my_roster), overall, opponents=opps, scoring_key=sk, max_slots=4)
        lines.append("    queue: " + " -> ".join("%s(%s)" % (s.name, s.position) for s in q))
        my_roster.append((top.name, top.position)); drafted.add(top.name); overall = target_pick + 1
    lines.append("\n=== ROSTER ===")
    for n, pos in my_roster: lines.append("  %-4s %s" % (pos, n))
    cliffs = [c for c in RL.tier_cliff(pool, drafted, my_roster, sk) if c.urgency != "ok"]
    lines.append("NEEDS: " + ("; ".join("%s(%s)"%(c.position,c.urgency) for c in cliffs) or "balanced"))
    lines.append("HANDCUFFS: " + ("; ".join("%s->%s"%(h.starter,h.backup) for h in RL.handcuffs(pool,drafted,my_roster,sk)) or "none"))
    byes=[b for b in RL.bye_collisions(my_roster,pool) if b.severity!="ok"]
    lines.append("BYE RISK: " + ("; ".join("wk%d(%s)"%(b.week,b.severity) for b in byes) or "clean"))
except Exception:
    lines.append("EXC:\n"+traceback.format_exc())
open("_drive.txt","w",encoding="utf-8").write("\n".join(lines))
