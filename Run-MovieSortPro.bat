@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 MovieSortPro.py
) else (
  python MovieSortPro.py
)
if errorlevel 1 pause
