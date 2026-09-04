@echo off
REM ============================================================
REM  Project Shredder - ONE-CLICK DRAFT LAUNCHER
REM  Starts BOTH the pick poller AND the app, in the right order,
REM  BEFORE your draft. This is the file to double-click on draft day.
REM
REM  What it does:
REM   1. Clears stale poller feed/status (so you start clean)
REM   2. Opens the POLLER in its own window (its own Chromium) - log
REM      into ESPN + open your draft room in THAT Chromium window.
REM   3. Opens the APP in its own window at http://localhost:8502
REM
REM  RUN THIS BEFORE THE DRAFT STARTS so it catches pick #1.
REM ============================================================
title Shredder Draft Launcher
cd /d "C:\Users\zem\.kiro\crew\workspace\fantasy_draft_assistant"
set PY="C:\Program Files\Python313\python.exe"

echo Clearing stale poller state...
del /q "data\live_picks.json" 2>nul
del /q "data\poller_status.json" 2>nul

echo Starting the pick poller (its own Chromium window will open)...
start "Shredder Poller" %PY% _dom_live_poller.py --persist --interval 2

echo Waiting a moment for the poller to come up...
timeout /t 4 /nobreak >nul

echo Starting the Shredder app at http://localhost:8502 ...
start "Shredder App" %PY% -m streamlit run app.py --server.port 8502

echo.
echo ============================================================
echo  BOTH STARTED. Now:
echo    1. In the POLLER's Chromium window - log into ESPN and
echo       open your DRAFT ROOM (that window is what gets scraped).
echo    2. In your normal browser, go to http://localhost:8502
echo       (no password) and pick the ESPN tab. Picks flow in live.
echo  Leave BOTH windows open for the whole draft.
echo ============================================================
echo.
pause
