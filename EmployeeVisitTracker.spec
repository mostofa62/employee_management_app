# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('logo.png', '.'), ('logo.svg', '.'), ('bangladesh-govt-logo.svg', '.'), ('fonts', 'fonts'), ('logo.ico', '.')],
    hiddenimports=['PIL', 'openpyxl', 'uharfbuzz', 'pymupdf', 'requests'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch','torchvision','torchaudio','ultralytics','tensorflow','scipy','matplotlib','sklearn','pandas','numpy','cv2','streamlit','altair','plotly','bokeh','notebook','jupyter','IPython','tkinter.test','sqlite3.test'],

    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='EmployeeVisitTracker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['logo.ico'],
)
