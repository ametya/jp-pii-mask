@echo off
rem ===================================================================
rem  JP PII Mask - prepare the app and the installer.
rem
rem  Just double-click this file. No commands to type.
rem  (Japanese users: the messages are printed by Python, in Japanese.)
rem ===================================================================
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)

if not defined PY (
    echo.
    echo Python was not found on this PC.
    echo Please install Python first, then run this file again.
    echo The download page will open in your browser.
    echo.
    start "" "https://www.python.org/downloads/"
    pause
    exit /b 1
)

%PY% "desktop\scripts\build-installer.py" %*
set "CODE=%ERRORLEVEL%"

echo.
if "%CODE%"=="0" (
    echo Done. Look for the files listed above.
) else (
    echo Something went wrong. Please read the messages above.
)
echo.
pause
exit /b %CODE%
