@echo off
setlocal
title QuickWB launcher

rem --- where the Python runtime lives (no admin, no PATH or registry changes) ---
rem     shared:  one Python + one package cache for every tool set up this way
rem     private: a self-contained folder only QuickWB uses
set "SHARED_ROOT=C:\ProgramData\PyApps"
set "PRIVATE_ROOT=C:\Users\Public\QuickWB"
set "PROJECT=%~dp0"
set "PYTHONPATH=%~dp0src"
set "ICON=%~dp0src\quickwb\assets\quickwb.ico"

rem --- pick a root. The folder on disk is the memory, so this is asked once. ---
rem     Set QUICKWB_RUNTIME to force a location of your own.
set "ROOT="
if defined QUICKWB_RUNTIME set "ROOT=%QUICKWB_RUNTIME%"
if not defined ROOT if exist "%PRIVATE_ROOT%\uv.exe" set "ROOT=%PRIVATE_ROOT%"
if not defined ROOT if exist "%SHARED_ROOT%\uv.exe"  set "ROOT=%SHARED_ROOT%"
if defined ROOT goto :have_root

echo.
echo   First run - where should Python and the packages be installed?
echo.
echo     [1] Shared    %SHARED_ROOT%
echo         Re-used by any other tool pointed at the same folder.
echo         Python and the package cache are stored once instead of once per
echo         app - roughly 1.5 GB saved for every extra app. Recommended.
echo.
echo     [2] Private   %PRIVATE_ROOT%
echo         Only QuickWB uses it, and uninstall.bat removes all of it.
echo.
set "PICK="
set /p "PICK=Choose 1 or 2 [1]: "
if "%PICK%"=="2" set "ROOT=%PRIVATE_ROOT%"
if not defined ROOT set "ROOT=%SHARED_ROOT%"

:have_root
set "UV=%ROOT%\uv.exe"
set "UV_PYTHON_INSTALL_DIR=%ROOT%\python"
set "UV_CACHE_DIR=%ROOT%\cache"
set "UV_PROJECT_ENVIRONMENT=%ROOT%\envs\quickwb"
rem the cache must sit on the same drive as the venv, or uv copies instead of
rem hardlinking and the sharing buys nothing
set "UV_NO_MODIFY_PATH=1"

rem --- one-time: put a QuickWB shortcut (with the app icon) on the Desktop ---
rem     resolve Desktop via .NET so a OneDrive-redirected / localized folder works;
rem     best-effort - a shortcut failure must never block launch
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "try {" ^
        "$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'QuickWB.lnk';" ^
        "if (-not (Test-Path $lnk)) {" ^
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($lnk);" ^
            "$s.TargetPath='%~f0'; $s.WorkingDirectory='%~dp0';" ^
            "$s.IconLocation='%ICON%'; $s.WindowStyle=7;" ^
            "$s.Description='QuickWB - image white balance'; $s.Save() }" ^
    "} catch {}"

rem --- one-time: fetch the uv binary ---
if not exist "%UV%" (
    echo First run: downloading uv into %ROOT% ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "New-Item -ItemType Directory -Force -Path '%ROOT%' | Out-Null;" ^
        "Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile '%ROOT%\uv.zip';" ^
        "Expand-Archive -Force '%ROOT%\uv.zip' '%ROOT%';" ^
        "Remove-Item '%ROOT%\uv.zip'"
    if not exist "%UV%" (
        echo Failed to download uv into %ROOT%.
        echo Check your internet connection, or whether that folder is writable.
        pause
        exit /b 1
    )
)

rem --- install Python + dependencies (fast no-op once done) ---
echo Preparing environment in %ROOT% ...
"%UV%" sync --project "%PROJECT%." --python 3.12
if errorlevel 1 (
    echo Environment setup failed.
    pause
    exit /b 1
)

rem --- launch the GUI with pythonw (no console window) and exit ---
start "" "%UV_PROJECT_ENVIRONMENT%\Scripts\pythonw.exe" -m quickwb
exit /b 0
