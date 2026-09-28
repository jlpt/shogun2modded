@echo off
rem Double-click to build toshima_clan.pack into your Shogun 2 data folder.
rem Extra options can be passed from a command prompt, e.g.  build_mod.bat --list-factions
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 build_mod.py %*
) else (
    python build_mod.py %*
)

echo.
pause
