@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (set "PYCMD=py -3") else (set "PYCMD=python")
%PYCMD% -m pip install --upgrade pyinstaller send2trash
if errorlevel 1 goto :fail
%PYCMD% -m PyInstaller --noconfirm --clean --onefile --windowed --name MovieSortPro MovieSortPro.py
if errorlevel 1 goto :fail
echo.
echo Your EXE is inside the dist folder: dist\MovieSortPro.exe
pause
exit /b 0
:fail
echo Build failed. Install Python for Windows from https://www.python.org/downloads/windows/
pause
exit /b 1
