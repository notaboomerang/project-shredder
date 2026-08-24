@echo off
REM Fantasy Draft Assistant launcher — double-click to run.
title Fantasy Draft Assistant
cd /d "C:\Users\zem\.kiro\crew\workspace\fantasy_draft_assistant"
echo Starting Fantasy Draft Assistant...
echo (Leave this window open while drafting. Close it or press Ctrl+C to stop.)
echo.
"C:\Program Files\Python313\python.exe" -m streamlit run app.py --server.port 8502
pause
