@echo off
setlocal
title QuickWB uninstaller

rem QuickWB.bat makes NO PATH or registry changes, so removing its runtime
rem folder removes every trace it left. Two cases:
rem   private root -> the whole folder is QuickWB's, delete it
rem   shared root  -> other apps use the Python and cache, delete only our venv
set "SHARED_ROOT=C:\ProgramData\PyApps"
set "PRIVATE_ROOT=C:\Users\Public\QuickWB"

set "MODE="
rem (parenthesised: without them the second set on the line runs unconditionally)
if exist "%SHARED_ROOT%\envs\quickwb" ( set "MODE=shared"  & set "TARGET=%SHARED_ROOT%\envs\quickwb" )
if exist "%PRIVATE_ROOT%\uv.exe"      ( set "MODE=private" & set "TARGET=%PRIVATE_ROOT%" )

if not defined MODE (
    echo Nothing to remove - no QuickWB runtime found in:
    echo     %PRIVATE_ROOT%
    echo     %SHARED_ROOT%\envs\quickwb
    pause
    exit /b 0
)

echo This will delete:
echo     %TARGET%
if "%MODE%"=="private" echo (private Python, virtual env, uv.exe and cache - approx 1.4 GB).
if "%MODE%"=="shared"  echo (only QuickWB's virtual env - the shared Python, uv.exe and cache
if "%MODE%"=="shared"  echo  in %SHARED_ROOT% stay, because other apps use them.)
echo.
echo Your images and exports are NOT touched.
echo.

set /p CONFIRM="Type Y to remove, anything else to cancel: "
if /I not "%CONFIRM%"=="Y" (
    echo Cancelled. Nothing was removed.
    pause
    exit /b 0
)

echo Removing ...
rmdir /s /q "%TARGET%"

rem also remove the Desktop shortcut if present
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'QuickWB.lnk';" ^
    "if (Test-Path $lnk) { Remove-Item $lnk -Force }"

if exist "%TARGET%" (
    echo.
    echo Could not fully remove it. Close QuickWB if it is running,
    echo then run this uninstaller again.
    pause
    exit /b 1
)

echo Done.
if "%MODE%"=="shared" (
    echo.
    echo The shared runtime is still there for your other apps:
    echo     %SHARED_ROOT%
    echo If QuickWB was the last one using it, delete that folder by hand.
)
pause
exit /b 0
