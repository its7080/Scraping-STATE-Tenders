@echo off
setlocal enabledelayedexpansion

REM Build standalone EXE for scraping.py using PyInstaller.
REM Run this from repository root in a prepared virtual environment.

echo [1/5] Upgrading build tooling...
python -m pip install --upgrade pip pyinstaller
if errorlevel 1 goto :fail

echo [2/5] Installing Python requirements...
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [3/5] Installing Playwright Chromium browser binaries...
set PLAYWRIGHT_BROWSERS_PATH=0
python -m playwright install chromium
if errorlevel 1 goto :fail

echo [4/5] Cleaning old build artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [5/6] Building executable from scraping.spec...
pyinstaller --clean scraping.spec
if errorlevel 1 goto :fail

echo [6/6] Building executable from scraping_gui.spec...
pyinstaller --clean scraping_gui.spec
if errorlevel 1 goto :fail

echo.
echo Build complete.
echo Engine EXE folder: dist\scraping\
echo Engine entry file: dist\scraping\scraping.exe
echo GUI EXE folder:    dist\scraping_gui\
echo GUI entry file:    dist\scraping_gui\scraping_gui.exe
exit /b 0

:fail
echo.
echo Build failed with errorlevel %errorlevel%.
exit /b %errorlevel%
