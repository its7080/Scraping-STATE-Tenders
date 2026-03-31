# Scraping-STATE-Tenders

State Tenders automation tool with:
- a Playwright-based scraping engine (`scraping.py`),
- a Windows desktop GUI (`scraping_gui.py`), and
- an OCR training/inference pipeline for CAPTCHA handling (`OCR/captcha_ocr_main.py`).

## Quick project map

- `scraping.py` — runtime scraping engine, emailing, merge/export logic.
- `scraping_gui.py` — CustomTkinter GUI for configuration and launch.
- `Program_Files/Configration.json` — engine/runtime settings.
- `Program_Files/search_criteria.json` — query criteria presets.
- `Program_Files/Organization_list.txt` — portal name/URL list.
- `OCR/` — OCR model + dataset utilities.

## Suggested setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Run

### GUI mode
```bash
python scraping_gui.py
```

### Engine mode
```bash
python scraping.py
```

## Basic checks

```bash
python -m unittest tests/test_validation_utils.py
python -m py_compile scraping.py scraping_gui.py OCR/captcha_ocr_main.py
```

## Build standalone `.exe` (Windows)

### Recommended (automated)
Use the provided build script:

```bat
build_exe.bat
```

This will:
- install build dependencies,
- install Playwright Chromium binaries with `PLAYWRIGHT_BROWSERS_PATH=0`,
- build via `scraping.spec` (engine) and `scraping_gui.spec` (GUI).

Output:
- folder: `dist\scraping\`
- executable: `dist\scraping\scraping.exe`
- folder: `dist\scraping_gui\`
- executable: `dist\scraping_gui\scraping_gui.exe`

### Manual build (if needed)
```bat
python -m pip install --upgrade pip pyinstaller
python -m pip install -r requirements.txt
set PLAYWRIGHT_BROWSERS_PATH=0
python -m playwright install chromium
pyinstaller --clean scraping.spec
pyinstaller --clean scraping_gui.spec
```

## Improvement backlog

A focused audit with concrete improvements is tracked in [`IMPROVEMENTS.md`](./IMPROVEMENTS.md).
