# -*- mode: python ; coding: utf-8 -*-
"""Конфигурация PyInstaller для портативной сборки калькулятора (--onedir).

Собирается папка dist/Calculator/ с файлом Calculator.exe, библиотеками и
ресурсами (шаблоны, статика, иконка, исходный Excel-шаблон прайса).
Изменяемые данные (база, прайс, экспорт, журналы) в сборку НЕ входят — они
создаются в папке пользователя %LOCALAPPDATA%\\PartitionCalculator при первом
запуске (см. app_paths.py).
"""

import os

SPEC_DIR = SPECPATH  # noqa: F821  — задаётся PyInstaller

datas = [
    (os.path.join(SPEC_DIR, "templates"), "templates"),
    (os.path.join(SPEC_DIR, "static"), "static"),
    (os.path.join(SPEC_DIR, "assets"), "assets"),
    # Исходный шаблон прайса: копируется в папку данных при первом запуске.
    (os.path.join(SPEC_DIR, "данные_калькулятора.xlsx"), "."),
]

hiddenimports = [
    "xlrd",                                  # читается лениво (формат .xls)
    "reportlab",
    "reportlab.pdfbase._fontdata",
    "reportlab.pdfbase.ttfonts",
    "reportlab.graphics.barcode",
    "PIL",
    "PIL.Image",
    "pystray",
    "pystray._win32",                        # значок в трее (Windows)
]

excludes = [
    "pytest", "_pytest", "tests",
    "pandas", "numpy", "matplotlib", "IPython",
    "tkinter",                               # не используется
]

a = Analysis(
    [os.path.join(SPEC_DIR, "run_app.py")],
    pathex=[SPEC_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Calculator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                            # оконное приложение, без консоли
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SPEC_DIR, "assets", "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Calculator",
)
