@echo off
setlocal enabledelayedexpansion

REM Build single-file EXEs for both scraping.py and scraping_gui.py.
REM Run this from repository root in a prepared virtual environment.

set "PLAYWRIGHT_BROWSERS_PATH=0"
set "EXTRA_DATA=--add-data Program_Files;Program_Files"
if exist OCR set "EXTRA_DATA=!EXTRA_DATA! --add-data OCR;Program_Files/OCR"

echo [1/6] Upgrading build tooling...
python -m pip install --upgrade pip pyinstaller
if errorlevel 1 goto :fail

echo [2/6] Installing Python requirements...
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [3/6] Installing Playwright Chromium browser binaries...
python -m playwright install chromium
if errorlevel 1 goto :fail

echo [4/6] Cleaning old build artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist __pycache__ rmdir /s /q __pycache__

echo [5/6] Building one-file EXE from scraping.py...
pyinstaller --noconfirm --clean --onefile --name scraping ^
    !EXTRA_DATA! ^
    --add-data Program_Files;Program_Files ^
    --add-data app_logo.ico;. ^
    --collect-all playwright ^
    --icon app_logo.ico ^
    scraping.py
if errorlevel 1 goto :fail

echo [6/6] Building one-file EXE from scraping_gui.py...
pyinstaller --noconfirm --clean --onefile --windowed --name scraping_gui ^
    !EXTRA_DATA! ^
    --add-data Program_Files;Program_Files ^
    --add-data app_logo.ico;. ^
    --collect-all playwright ^
    --collect-all customtkinter ^
    --icon app_logo.ico ^
    scraping_gui.py
if errorlevel 1 goto :fail

echo.
echo Build complete.
echo Engine EXE (single file): dist\scraping.exe
echo GUI EXE (single file):    dist\scraping_gui.exe
echo.
echo Both EXEs are independent and can be run at the same time.
exit /b 0

:fail
echo.
echo Build failed with errorlevel %errorlevel%.
exit /b %errorlevel%
