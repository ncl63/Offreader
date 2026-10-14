@echo off
rem Construit dist\Offreader\Offreader.exe (dossier autonome, Chromium embarque).
setlocal
cd /d "%~dp0"

if not exist ".venv-build\Scripts\python.exe" (
  py -3 -m venv .venv-build || goto :fail
)
set "PYB=.venv-build\Scripts\python.exe"
"%PYB%" -m pip install --disable-pip-version-check -q -r requirements-gui.txt pyinstaller || goto :fail

rem Chromium installe DANS le paquet playwright, pour etre embarque par PyInstaller.
set PLAYWRIGHT_BROWSERS_PATH=0
rem --only-shell : seule la version sans fenetre est utilisee par l'application (~270 Mo de moins).
"%PYB%" -m playwright install chromium --only-shell || goto :fail

"%PYB%" -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\offreader.spec || goto :fail

echo.
echo Termine : dist\Offreader\Offreader.exe
echo (copiez tout le dossier dist\Offreader, pas seulement le .exe)
exit /b 0

:fail
echo La construction a echoue.
exit /b 1
