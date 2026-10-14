@echo off
rem Lance l'application Offreader. Premier lancement : installation automatique
rem (environnement Python local + dependances + Chromium), quelques minutes.
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (where python >nul 2>&1 && set "PY=python")
if not defined PY (
  echo Python 3.12 ou plus recent est introuvable.
  echo Installez-le depuis https://www.python.org/downloads/ en cochant "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\pythonw.exe" (
  echo Premiere utilisation : preparation de l'environnement...
  %PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" || (
    echo Python 3.12 ou plus recent est requis.
    pause
    exit /b 1
  )
  %PY% -m venv .venv || goto :fail
  del ".venv\.offreader-ready" >nul 2>&1
)

if not exist ".venv\.offreader-ready" (
  echo Installation des dependances...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements-gui.txt || goto :fail
  echo Installation de Chromium...
  ".venv\Scripts\python.exe" -m playwright install chromium
  if errorlevel 1 echo Chromium n'a pas pu etre installe : Microsoft Edge sera utilise a la place.
  echo ok> ".venv\.offreader-ready"
)

start "" ".venv\Scripts\pythonw.exe" "%~dp0offreader_gui.pyw"
exit /b 0

:fail
echo.
echo L'installation a echoue. Verifiez la connexion Internet puis relancez Offreader.bat.
pause
exit /b 1
