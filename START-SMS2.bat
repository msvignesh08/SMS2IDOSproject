@echo off
REM ===========================================================================
REM  SMS-2 IDOS — start the plant server on this PC
REM
REM  Double-click this file. Leave the window open: closing it stops the
REM  server. Other machines on the same network reach it at the address
REM  printed below.
REM
REM  Why local rather than a hosted free tier: reading a full-year workbook
REM  peaks around 250 MB and a 512 MB instance has to serve the whole app in
REM  that budget. This PC has 16 GB.
REM ===========================================================================

cd /d "%~dp0backend"

REM -- PLANT CLOCK ------------------------------------------------------------
REM  Without this the shift engine reads the machine's own clock. It matters:
REM  the plant day runs 22:00 to 22:00, so a wrong timezone files heats against
REM  the wrong shift AND the wrong day.
set SMS2_PLANT_TZ=Asia/Kolkata

REM -- PASSWORDS --------------------------------------------------------------
REM  Set these before anyone outside your desk can reach the server, or the
REM  seeded demo passwords from app/seed.py become the live ones.
REM  Remove the REM to use them:
REM set SMS2_ADMIN_PASSWORD=change-this
REM set SMS2_SEED_PASSWORD=change-this-too

REM -- AI (optional) ----------------------------------------------------------
REM  Leave unset and the offline rules engine answers, as it always has.
REM set ANTHROPIC_API_KEY=

echo.
echo   SMS-2 IDOS starting...
echo.
echo   On this PC        http://localhost:8000
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
  for /f "tokens=1" %%b in ("%%a") do echo   On the network    http://%%b:8000
)
echo.
echo   Keep this window open. Ctrl+C or closing it stops the server.
echo.

REM  --host 0.0.0.0 listens on every network interface, which is what lets
REM  other machines connect. One worker is deliberate: the app holds state in
REM  process, and a second worker would answer with a different copy of it.
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1

echo.
echo   Server stopped.
pause
