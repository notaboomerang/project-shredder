"""
Project Shredder -- ESPN live-draft DOM poller (the durable live-sync path).

WHY DOM-SCRAPE (not REST, not SSE)
  Confirmed 2026-08-25 from live practice-draft captures:
   - REST mDraftDetail returns playerId:-1 placeholders during a live draft.
   - Picks stream over an undocumented SSE channel (one connection per session).
  Scraping the draft room's own rendered "Picks" panel avoids both problems:
  it reads exactly what a human sees, needs no session token, survives ESPN
  protocol changes, and runs ALONGSIDE the user's open draft room.

WHAT IT DOES
  Opens the ESPN draft room in a Chromium with the user's saved cookies, polls
  the pick feed (li.pick-message__container) every INTERVAL seconds, and appends
  any NEW pick lines to data/live_picks.json. Each line is in the exact format
  the app's bulk-paste parser already handles:
      "Jahmyr Gibbs / DET RB   R1, P1 - Team Floyd"
  The Streamlit app tails live_picks.json (live_dom_sync.read_new_picks) and
  feeds new picks through the same _record_pick ingestion path.

USAGE
  python _dom_live_poller.py --url "<draft room URL>" [--interval 2] [--secs 0]
    --url    : the ESPN draft room URL (from the draft-room browser tab)
    --interval: poll seconds (default 2)
    --secs   : stop after N seconds (0 = run until the draft completes / Ctrl-C)

  Cookies are read from data/espn_cookies.json (same store as the app).
  Output: data/live_picks.json  (JSON list of {overall, round, player, team_label})
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_OUT = os.path.join(_HERE, "data", "live_picks.json")
_STATUS = os.path.join(_HERE, "data", "poller_status.json")

# "Jahmyr Gibbs / DET RB   R1, P1 - Team Floyd"
_PICK_RE = re.compile(
    r"^(?P<player>.+?)\s*/\s*(?P<team>[A-Z]{2,3})\s+(?P<pos>[A-Z/]+)\s+"
    r"R(?P<rd>\d+),\s*P(?P<pk>\d+)\s*[-\u2013]\s*(?P<owner>.+?)$"
)


def load_cookies() -> tuple[str, str]:
    path = os.path.join(_HERE, "data", "espn_cookies.json")
    s2 = swid = ""
    if os.path.exists(path):
        with open(path) as f:
            j = json.load(f)
        s2 = j.get("espn_s2", "") or j.get("s2", "")
        swid = j.get("swid", "") or j.get("SWID", "")
    if swid:
        swid = "{" + swid.strip("{}").strip() + "}"
    return s2, swid


def parse_pick(text: str) -> dict | None:
    """Parse one pick row's innerText into a structured dict, or None."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    m = _PICK_RE.match(t)
    if not m:
        return None
    rd = int(m.group("rd"))
    slot = int(m.group("pk"))   # ESPN's "P#" = pick-WITHIN-round (slot), NOT overall
    # We don't reliably know team count / snake direction from the DOM, so we do
    # NOT fabricate a true overall number here. Instead we emit a collision-free
    # ORDER key = round*1000 + slot, which sorts picks correctly (R1 before R2,
    # slot 1 before slot 2 within a round). The app keys ingestion off the
    # player NAME (dedup), so this only needs to be unique + monotonic, which
    # (round, slot) guarantees. Storing the raw round + slot lets the app compute
    # a true overall later if it knows the league size.
    return {
        "player": m.group("player").strip(),
        "team": m.group("team"),
        "pos": m.group("pos"),
        "round": rd,
        "slot": slot,
        "overall": rd * 1000 + slot,   # ORDER key only (unique, monotonic)
        "owner": m.group("owner").strip(),
        "raw": t,
    }


def write_picks(picks: list[dict]) -> None:
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    tmp = _OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"updated": time.time(), "picks": picks}, f, indent=2)
    os.replace(tmp, _OUT)  # atomic so the app never reads a half-written file


def write_status(state: str, tab_url: str = "", picks: int = 0) -> None:
    """Heartbeat the poller's readiness so the app can show a pre-draft light.
    state: 'launching' | 'waiting_for_draft' | 'attached' | 'live'."""
    try:
        os.makedirs(os.path.dirname(_STATUS), exist_ok=True)
        tmp = _STATUS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"updated": time.time(), "state": state,
                       "tab_url": tab_url, "picks": picks}, f)
        os.replace(tmp, _STATUS)
    except Exception:
        pass


def _norm_name(s: str) -> str:
    """Same normalization the app-side reader uses: strip generational suffix
    tokens (Jr/Sr/II/III/IV/V) BEFORE collapsing to bare alnum, so REST and
    ticker spellings of the same player ('Kenneth Walker III' vs 'Kenneth
    Walker') dedup to one pick."""
    s = (s or "").lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)


def merge_backfill(all_picks: list[dict], seen: set[str], url: str) -> int:
    """Pull every COMPLETED pick from ESPN's REST draftDetail and merge any we
    don't already have (by normalized player name) into `all_picks`. This is
    what makes attaching MID-DRAFT safe: the rolling ticker only shows recent
    picks, so without this the early rounds are missing. Returns how many NEW
    picks were added. Silent no-op if the REST source is unavailable."""
    try:
        import live_backfill as _bf
        rows = _bf.backfill_picks(url=url)
    except Exception as ex:
        print("[backfill] unavailable:", ex)
        return 0
    if not rows:
        return 0
    have = {_norm_name(p.get("player", "")) for p in all_picks}
    added = 0
    for r in rows:
        nk = _norm_name(r.get("player", ""))
        if not nk or nk in have:
            continue
        have.add(nk)
        seen.add((r.get("raw", "") or "").lower())  # keep ticker from re-adding
        all_picks.append(r)
        added += 1
        print(f"  [rest] P{r['overall']:>3} {r['player']} ({r['pos']}) -> {r['owner']}")
    if added:
        all_picks.sort(key=lambda x: x.get("overall", 0))
        write_picks(all_picks)
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="ESPN draft room URL (launch mode)")
    ap.add_argument("--cdp", help="Attach to your already-open Chrome via CDP, "
                    "e.g. http://127.0.0.1:9222 . Scrapes the EXISTING draft tab "
                    "so ESPN does not kick it as a Duplicate Connection.")
    ap.add_argument("--persist", action="store_true",
                    help="ONE-BUTTON MODE: launch our OWN persistent Chromium "
                    "(profile in data/espn_profile). You log into ESPN + open your "
                    "draft in THAT window once; login sticks for every future draft. "
                    "The poller reads that same tab -- no second connection, no "
                    "Duplicate-Connection kick, no debug port, no cookies file.")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--secs", type=int, default=0, help="0 = until complete/Ctrl-C")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed. pip install playwright && "
              "python -m playwright install chromium", file=sys.stderr)
        sys.exit(1)

    seen: set[str] = set()          # normalized raw lines already emitted
    all_picks: list[dict] = []
    deadline = time.time() + args.secs if args.secs > 0 else None

    # ---- Extraction --------------------------------------------------------
    # The rolling ticker (li.pick-message__container) only holds RECENT picks, so
    # attaching mid-draft loses rounds 1..N (the phantom-available bug). To get
    # EVERY pick we also scrape ESPN's full draft board / pick-history DOM.
    #
    # ESPN's draft room renders the complete board as a grid of per-pick cells
    # under the "Pick History" / board view. We probe several candidate selectors,
    # log how many rows each yields, and use the richest one -- so the poller
    # self-discovers the right source against the live DOM instead of guessing.
    #
    # Each candidate returns an array of {overall, text} where text is the cell's
    # innerText. The board cells carry player + team + pos + owner; the pick
    # number comes from the cell's own numbering when present, else from the
    # parsed "R#, P#" in the text.
    JS_BOARD = r"""() => {
      const out = [];
      const seen = new Set();
      const push = (t) => {
        t = (t || '').replace(/\s+/g,' ').trim();
        if (t && !seen.has(t)) { seen.add(t); out.push(t); }
      };
      // Candidate 1: full pick-history rows (the durable, complete list).
      document.querySelectorAll(
        '.draft-columns__pick, .pick-history__pick, [class*="pickHistory"] [class*="pick"], '
        + '.draftboard__cell, [class*="DraftBoard"] [class*="cell"], '
        + 'li.pick-message__container'
      ).forEach(el => push(el.innerText));
      return out;
    }"""

    def _dump_dom_once(pg):
        """One-shot: dump candidate selector hit-counts + a DOM sample so we can
        confirm the real full-history selector from the log."""
        try:
            info = pg.evaluate(r"""() => {
              const cand = [
                'li.pick-message__container',
                '.draft-columns__pick', '.pick-history__pick',
                '[class*="pickHistory"]', '[class*="PickHistory"]',
                '.draftboard__cell', '[class*="DraftBoard"]',
                '[class*="draftBoard"]', '[class*="draft-board"]',
                '[class*="pick"]',
              ];
              const counts = {};
              cand.forEach(c => { try { counts[c] = document.querySelectorAll(c).length; } catch(e){ counts[c] = 'ERR'; } });
              // grab a couple of sample innerTexts from the biggest 'pick'-ish set
              let sample = [];
              const big = document.querySelectorAll('[class*="pick"]');
              for (let i=0; i<Math.min(6, big.length); i++) {
                sample.push((big[i].className||'') + ' :: ' + (big[i].innerText||'').replace(/\s+/g,' ').slice(0,80));
              }
              return {counts, sample, tabs: Array.from(document.querySelectorAll('[role="tab"],button,a'))
                        .map(e=>(e.innerText||'').trim()).filter(t=>/history|board|pick/i.test(t)).slice(0,20)};
            }""")
            with open(os.path.join(_HERE, "data", "dom_probe.json"), "w", encoding="utf-8") as f:
                json.dump(info, f, indent=2)
            print("[probe] selector hit-counts:", json.dumps(info.get("counts", {})))
            print("[probe] pick-ish samples:", info.get("sample", []))
            print("[probe] history/board controls:", info.get("tabs", []))
        except Exception as ex:
            print("[probe] dump failed:", ex)

    # Legacy rolling-ticker extractor (still used as a fallback / live delta).
    JS = """() => Array.from(document.querySelectorAll('li.pick-message__container'))
                 .map(el => (el.innerText || '').replace(/\\s+/g,' ').trim())
                 .filter(Boolean)"""

    with sync_playwright() as pw:
        if args.persist:
            # ONE-BUTTON MODE: our own persistent Chromium. Login persists in the
            # profile dir, so the user signs into ESPN once, ever. We then wait
            # for them to navigate to their draft room and scrape THAT page. No
            # second connection to the draft (this IS the draft browser), so ESPN
            # never issues a Duplicate-Connection kick.
            profile = os.path.join(_HERE, "data", "espn_profile")
            os.makedirs(profile, exist_ok=True)
            print(f"[persist] launching Chromium (profile: {profile})")
            write_status("launching")

            def _clear_profile_lock(prof):
                # A crashed/leftover Chromium leaves Singleton* lock files that make
                # the next launch fail with 'profile already in use'. Safe to remove
                # when no live Chromium holds them (we only reach here on that error).
                import glob as _glob
                removed = []
                for pat in ("SingletonLock", "SingletonCookie", "SingletonSocket",
                            "lockfile", ".org.chromium.*"):
                    for f in _glob.glob(os.path.join(prof, pat)):
                        try:
                            os.remove(f); removed.append(os.path.basename(f))
                        except Exception:
                            pass
                return removed

            def _launch():
                return pw.chromium.launch_persistent_context(
                    profile, headless=args.headless,
                    args=["--no-first-run", "--no-default-browser-check"],
                    viewport={"width": 1400, "height": 900},
                )

            try:
                ctx = _launch()
            except Exception as _lockex:
                msg = str(_lockex)
                if "already in use" in msg or "ProcessSingleton" in msg or "SingletonLock" in msg:
                    cleared = _clear_profile_lock(profile)
                    print(f"[persist] stale profile lock detected; cleared {cleared}; retrying...")
                    write_status("relaunching")
                    time.sleep(1)
                    try:
                        ctx = _launch()
                    except Exception as _lockex2:
                        print(f"[persist] FATAL: profile still locked after clearing: {_lockex2}",
                              file=sys.stderr)
                        write_status("error_profile_locked")
                        sys.exit(3)
                else:
                    print(f"[persist] FATAL launch error: {msg}", file=sys.stderr)
                    write_status("error_launch")
                    raise
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            # Seed saved cookies too (harmless if the profile is already logged in)
            s2, swid = load_cookies()
            if s2 or swid:
                cookies = []
                for name, val in (("espn_s2", s2), ("SWID", swid)):
                    if val:
                        for dom in (".espn.com", ".fantasy.espn.com"):
                            cookies.append({"name": name, "value": val,
                                            "domain": dom, "path": "/"})
                try:
                    ctx.add_cookies(cookies)
                except Exception as ex:
                    print("[persist] add_cookies:", ex)
            # Land them on the fantasy home (or the given --url) so they can log
            # in / navigate to their draft.
            start = args.url or "https://fantasy.espn.com/football/"
            try:
                page.goto(start, wait_until="domcontentloaded", timeout=45000)
            except Exception as ex:
                print("[persist] initial nav:", ex)
            print("[persist] >>> Log into ESPN in this window and OPEN YOUR DRAFT "
                  "ROOM. Waiting for a draft tab...")
            write_status("waiting_for_draft")

            # The draft room can be at /football/draft OR the mock-draft lobby can
            # navigate/popup into it; ESPN also serves the live room from the
            # fantasydraft host. Match any of those, and detect the pick feed DOM
            # as a last resort (some draft URLs don't carry a stable path).
            def _is_draft_url(u: str) -> bool:
                u = (u or "").lower()
                # ONLY the actual draft room. The team/league/lobby pages contain
                # the word "draft" in params or copy and must NOT match.
                return ("/football/draft" in u
                        or "fantasydraft.espn.com" in u)

            def _find_draft_page():
                # Track popups too: ESPN's "Practice Draft" button opens a new
                # window (window.open). launch_persistent_context surfaces those
                # as new pages in ctx.pages, but they may start as about:blank and
                # navigate a beat later -- so also probe for the pick-feed DOM.
                for pg in ctx.pages:
                    try:
                        url = pg.url
                    except Exception:
                        continue
                    if _is_draft_url(url):
                        return pg
                # Fallback: any tab whose DOM has the actual pick-feed container
                # (the draft room's rendered picks list). Do NOT match a generic
                # [class*='draft'] -- the team/lobby pages carry that too.
                for pg in ctx.pages:
                    try:
                        if pg.query_selector("li.pick-message__container"):
                            if "mockdraftlobby" not in (pg.url or "").lower():
                                return pg
                    except Exception:
                        continue
                return None

            # Wait (up to 15 min) for the draft room to appear in any tab.
            #
            # SHORT-CIRCUIT: if the user pasted an explicit --url, we already
            # navigated THIS page to it, so scrape it directly instead of hunting
            # for a "draft tab". This is the paste-the-URL flow: the page we're on
            # IS the draft room. We still give it a few seconds to finish any
            # redirect, but we NEVER exit/close on a URL-matcher miss -- that was
            # the bug that made the window vanish for practice/mock rooms whose
            # URL doesn't contain '/football/draft'.
            if args.url:
                # brief settle for client-side redirects, then use whatever tab
                # currently holds the pick feed, else the page we navigated.
                for _ in range(8):
                    time.sleep(1)
                    cand = _find_draft_page()
                    if cand is not None:
                        page = cand
                        break
                else:
                    page = _find_draft_page() or page
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                print(f"[persist+url] scraping pasted draft URL tab: {page.url[:80]}")
                _keep_open = True
            else:
                waited = 0
                dp = _find_draft_page()
                _last_report = ""
                while dp is None and waited < 900:
                    time.sleep(2); waited += 2
                    # Every ~20s, log what tabs ARE open so we can diagnose a stall.
                    if waited % 20 == 0:
                        try:
                            urls = " | ".join((p.url or "")[:60] for p in ctx.pages)
                        except Exception:
                            urls = "(couldn't read tabs)"
                        if urls != _last_report:
                            print(f"[persist] waiting ({waited}s). open tabs: {urls}")
                            _last_report = urls
                    dp = _find_draft_page()
                if dp is None:
                    print("[persist] no draft room opened within 15 min; exiting.",
                          file=sys.stderr)
                    try:
                        ctx.close()
                    except Exception:
                        pass
                    sys.exit(2)
                page = dp
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                print(f"[persist] found draft tab: {page.url[:80]}")
                write_status("attached", tab_url=page.url)
                _keep_open = True  # never close the user's draft browser
        elif args.cdp:
            # ATTACH mode: connect to the user's already-running Chrome and find
            # the EXISTING draft tab. No second connection -> no Duplicate kick.
            print(f"[cdp] connecting to {args.cdp}")
            browser = pw.chromium.connect_over_cdp(args.cdp)
            page = None
            for ctx in browser.contexts:
                for pg in ctx.pages:
                    if "/football/draft" in pg.url:
                        page = pg
                        break
                if page:
                    break
            if page is None:
                print("[cdp] no open '/football/draft' tab found. Open your ESPN "
                      "draft room in that Chrome first, then rerun.", file=sys.stderr)
                sys.exit(2)
            print(f"[cdp] attached to draft tab: {page.url[:80]}")
        else:
            # LAUNCH mode (fallback / testing only -- will hit Duplicate Connection
            # if your own draft tab is already open for the same team).
            if not args.url:
                print("ERROR: pass --cdp <endpoint> (preferred) or --url <draft url>",
                      file=sys.stderr)
                sys.exit(1)
            s2, swid = load_cookies()
            print(f"[cookies] espn_s2={'set' if s2 else 'MISSING'} swid={'set' if swid else 'MISSING'}")
            cookies = []
            for name, val in (("espn_s2", s2), ("SWID", swid)):
                if val:
                    for dom in (".espn.com", ".fantasy.espn.com"):
                        cookies.append({"name": name, "value": val, "domain": dom, "path": "/"})
            browser = pw.chromium.launch(headless=args.headless)
            ctx = browser.new_context()
            if cookies:
                try:
                    ctx.add_cookies(cookies)
                except Exception as ex:
                    print("[warn] add_cookies:", ex)
            page = ctx.new_page()
            print(f"[nav] {args.url}")
            page.goto(args.url, wait_until="domcontentloaded", timeout=45000)

        print("[poll] watching pick feed... (Ctrl-C to stop)")
        _dump_dom_once(page)  # one-shot: reveal the real full-history selectors

        # ---- REST backfill on connect: seed EVERY completed pick so attaching
        # mid-draft doesn't miss the early rounds the ticker already scrolled
        # past. Uses the draft tab's own URL to find the leagueId.
        try:
            _bf_url = page.url or args.url or ""
        except Exception:
            _bf_url = args.url or ""
        n0 = merge_backfill(all_picks, seen, _bf_url)
        if n0:
            print(f"[backfill] seeded {n0} completed pick(s) from REST on connect")
        else:
            print("[backfill] none from REST on connect (ticker-only mode)")

        empty_streak = 0
        _bf_idle_top = 0
        try:
            while True:
                if deadline and time.time() > deadline:
                    print("[poll] time limit reached"); break
                # Heartbeat: attached to a draft tab and watching. picks>0 => live.
                try:
                    _turl = page.url or _bf_url
                except Exception:
                    _turl = _bf_url
                write_status("live" if all_picks else "attached",
                             tab_url=_turl, picks=len(all_picks))
                # Prefer the full board/history extractor (captures ALL picks incl.
                # round 1); fall back to the rolling ticker if it yields nothing.
                try:
                    rows = page.evaluate(JS_BOARD)
                    if not rows:
                        rows = page.evaluate(JS)
                except Exception as ex:
                    print("[poll] evaluate error (retrying):", ex)
                    time.sleep(args.interval); continue

                new = 0
                for txt in rows:
                    key = txt.lower()
                    if key in seen:
                        continue
                    p = parse_pick(txt)
                    if not p:
                        continue
                    seen.add(key)
                    all_picks.append(p)
                    new += 1
                    print(f"  + P{p['overall']:>3} {p['player']} ({p['pos']}) -> {p['owner']}")
                if new:
                    all_picks.sort(key=lambda x: x["overall"])
                    write_picks(all_picks)
                    empty_streak = 0
                else:
                    empty_streak += 1
                if empty_streak and empty_streak % 30 == 0:
                    print(f"[poll] {len(all_picks)} picks so far, idle {empty_streak} ticks")
                # Periodic REST top-up: every ~15 ticks re-pull completed picks so
                # anything the ticker scrolled past between polls is still caught.
                # Cheap (one REST call) and deduped by name, so it's safe to repeat.
                _bf_idle_top += 1
                if _bf_idle_top >= 15:
                    _bf_idle_top = 0
                    try:
                        _u = page.url or _bf_url
                    except Exception:
                        _u = _bf_url
                    added = merge_backfill(all_picks, seen, _u)
                    if added:
                        print(f"[backfill] top-up added {added} pick(s) the ticker missed")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[poll] stopped by user")
        finally:
            if all_picks:
                write_picks(all_picks)
            print(f"[done] {len(all_picks)} picks written to {_OUT}")
            # In CDP/persist mode do NOT close the user's browser -- just detach.
            try:
                if not args.cdp and not args.persist:
                    browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
