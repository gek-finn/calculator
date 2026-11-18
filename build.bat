@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo   Sborka portativnogo prilozheniya "Kalkulyator LiderPro"
echo   (PyInstaller, rezhim --onedir)
echo ============================================================
echo.

rem --- 1. Virtualnoe okruzhenie ---
if exist ".venv\Scripts\python.exe" goto :have_venv
where python >nul 2>nul
if errorlevel 1 goto :no_python
echo Sozdayu virtualnoe okruzhenie .venv ...
python -m venv .venv
if errorlevel 1 goto :venv_fail

:have_venv
if not exist ".venv\Scripts\pip.exe" goto :pip_fail

rem --- 2. Zavisimosti prilozheniya ---
".venv\Scripts\python.exe" -c "import flask, openpyxl, xlrd, reportlab, pypdf" >nul 2>nul
if not errorlevel 1 goto :deps_ok
echo Ustanavlivayu zavisimosti prilozheniya (requirements.txt) ...
".venv\Scripts\pip.exe" install -q -r requirements.txt
if errorlevel 1 goto :deps_fail
:deps_ok

rem --- 3. Zavisimosti sborki (PyInstaller) ---
".venv\Scripts\python.exe" -c "import PyInstaller" >nul 2>nul
if not errorlevel 1 goto :builddeps_ok
echo Ustanavlivayu PyInstaller (requirements-build.txt) ...
".venv\Scripts\pip.exe" install -q -r requirements-build.txt
if errorlevel 1 goto :deps_fail
:builddeps_ok

rem --- 4. Ikonka (zagotovka, esli net svoei) ---
if exist "assets\app.ico" goto :icon_ok
echo Sozdayu zagotovku ikonki assets\app.ico ...
".venv\Scripts\python.exe" tools\make_icon.py
:icon_ok

rem --- 5. Ochistka ---
echo Ochishchayu predydushchie sborki (build, dist) ...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

rem --- 6. Sborka ---
echo.
echo Zapuskayu PyInstaller (eto mozhet zanyat neskolko minut) ...
echo.
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean Calculator.spec
if errorlevel 1 goto :build_fail

if not exist "dist\Calculator\Calculator.exe" goto :build_fail

rem --- 6b. Kratkaya instrukciya dlya polzovatelya ---
".venv\Scripts\python.exe" tools\prepare_dist.py

rem --- 7. ZIP dlya peredachi ---
echo.
echo Upakovyvayu v arhiv ...
".venv\Scripts\python.exe" tools\package_zip.py "dist\Calculator" "dist\Calculator-portable.zip"
if errorlevel 1 (
  echo [PREduprezhdenie] Ne udalos sozdat ZIP, no papka sborki gotova.
)

echo.
echo ============================================================
echo   GOTOVO
echo   Papka:  dist\Calculator\
echo   EXE:    dist\Calculator\Calculator.exe
echo   Arhiv:  dist\Calculator-portable.zip
echo ============================================================
echo.
echo Dlya zapuska otkroite Calculator.exe dvoynym shchelchkom.
echo Dannye hranyatsya v %%LOCALAPPDATA%%\PartitionCalculator
echo.
pause
exit /b 0

:no_python
echo [ERROR] Python not found. Ustanovite Python 3.11+ i povtorite.
pause
exit /b 1

:venv_fail
echo [ERROR] Ne udalos sozdat virtualnoe okruzhenie .venv
pause
exit /b 1

:pip_fail
echo [ERROR] V .venv otsutstvuet pip.
pause
exit /b 1

:deps_fail
echo [ERROR] Ne udalos ustanovit zavisimosti. Proverte internet i povtorite.
pause
exit /b 1

:build_fail
echo [ERROR] Sborka PyInstaller zavershilas s oshibkoy. Sm. soobshcheniya vyshe.
pause
exit /b 1
