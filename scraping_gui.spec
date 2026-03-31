# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for building a standalone Windows EXE for scraping_gui.py.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

datas = []
datas += [("Program_Files", "Program_Files")]
datas += [("OCR", "OCR")]
datas += [("app_logo.ico", ".")]
datas += collect_data_files("playwright")
datas += collect_data_files("customtkinter")

hiddenimports = []
hiddenimports += collect_submodules("playwright")
hiddenimports += collect_submodules("customtkinter")

a = Analysis(
    ["scraping_gui.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="scraping_gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon="app_logo.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="scraping_gui",
)
