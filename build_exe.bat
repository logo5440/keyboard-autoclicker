@echo off
setlocal
cd /d "%~dp0"
title Keyboard Autoclicker - build EXE and installer

if not exist "keyboard_autoclicker.py" (
    echo ERROR: keyboard_autoclicker.py is not in the same folder as this file.
    goto :failed
)
if not exist "icon.ico" (
    echo ERROR: icon.ico is not in the same folder as this file.
    goto :failed
)

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo ERROR: Python was not found.
    echo Please install it from https://www.python.org and tick
    echo "Add python.exe to PATH" in the first window of the Python setup.
    goto :failed
)

echo.
echo [1/3] Installing dependencies ...
%PY% -m pip install --upgrade pip pynput pillow pyinstaller
if errorlevel 1 goto :failed

echo.
echo [2/3] Building the EXE (no console window, with icon) ...
%PY% -m PyInstaller --noconfirm --clean --onefile --noconsole --name KeyboardAutoclicker --icon icon.ico --add-data "icon.ico;." --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 --hidden-import PIL._tkinter_finder keyboard_autoclicker.py
if errorlevel 1 goto :failed
if not exist "dist\KeyboardAutoclicker.exe" (
    echo ERROR: dist\KeyboardAutoclicker.exe was not created.
    goto :failed
)

echo.
echo [3/3] Building the installer ...
set "ISCC="
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"

if not defined ISCC (
    echo.
    echo Inno Setup 6 was not found, so only the EXE was created:
    echo   dist\KeyboardAutoclicker.exe
    echo To build the installer, install Inno Setup 6
    echo   https://jrsoftware.org/isdl.php
    echo and run this file again.
    pause
    exit /b 0
)

"%ISCC%" installer.iss
if errorlevel 1 goto :failed

echo.
echo FINISHED!
echo   Program:   dist\KeyboardAutoclicker.exe
echo   Installer: Output\KeyboardAutoclicker_Setup.exe
pause
exit /b 0

:failed
echo.
echo An error occurred. Please read the messages above.
pause
exit /b 1
