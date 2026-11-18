@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo   Kalkulyator "LiderPro" - lokalnyi zapusk
echo ============================================================

where python >nul 2>nul
if errorlevel 1 goto :no_python

if exist ".venv\Scripts\python.exe" goto :deps_check
python -c "print('Sozdayu virtualnoe okruzhenie...')"
python -m venv .venv
if errorlevel 1 goto :venv_fail

:deps_check
if not exist ".venv\Scripts\pip.exe" goto :pip_fail
".venv\Scripts\python.exe" -c "import flask, openpyxl, reportlab, pypdf" >nul 2>nul
if not errorlevel 1 goto :run

python -c "print('Ustanavlivayu zavisimosti iz requirements.txt (pervyi zapusk)...')"
".venv\Scripts\pip.exe" install -q -r requirements.txt
if errorlevel 1 goto :deps_fail

:run
echo.
python -c "print('Zapuskayu prilozhenie...')"
".venv\Scripts\python.exe" app.py
echo.
echo ============================================================
python -c "print('Prilozhenie ostanovleno. Baza i Excel-fail sohraneny.')"
echo ============================================================
pause
exit /b 0

:no_python
echo [ERROR] Python not found.
python -c "" 2>nul
echo Install Python 3.11+ from https://www.python.org/downloads/
echo and check "Add python.exe to PATH" during installation.
pause
exit /b 1

:venv_fail
echo [ERROR] Failed to create virtual environment.
pause
exit /b 1

:pip_fail
echo [ERROR] pip is missing in the virtual environment.
pause
exit /b 1

:deps_fail
echo [ERROR] Failed to install dependencies.
echo Check your internet connection and try again.
pause
exit /b 1
