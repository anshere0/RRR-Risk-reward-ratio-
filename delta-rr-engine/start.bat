@echo off
title Delta BTC Options Risk-Reward Engine
cd /d "%~dp0"

echo =======================================================================
echo     DELTA BTC OPTIONS RISK-REWARD ENGINE (24/7 BACKGROUND SERVER)
echo =======================================================================
echo.
echo  Starting local engine on http://127.0.0.1:8000 ...
echo  Keep this terminal window open while trading Delta Exchange!
echo.
echo  - Sidelay Extension connects automatically: http://127.0.0.1:8000
echo  - Press Ctrl+C anytime to stop.
echo =======================================================================
echo.

.\.venv\Scripts\python.exe -m rr_engine
pause
