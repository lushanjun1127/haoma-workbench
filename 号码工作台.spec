# -*- mode: python ; coding: utf-8 -*-
import os

# The app imports only PyQt6.QtWidgets / QtCore / QtGui and uses no icons, SVG,
# PDF, multimedia or network. Everything listed here is never imported, so
# excluding it keeps those modules out of the bundle (the spec-file equivalent
# of --exclude-module).
EXCLUDES = [
    'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebEngineQuick',
    'PyQt6.QtMultimedia', 'PyQt6.QtMultimediaWidgets',
    'PyQt6.QtQml', 'PyQt6.QtQuick', 'PyQt6.QtQuickWidgets', 'PyQt6.QtQuick3D',
    'PyQt6.QtSql', 'PyQt6.QtTest', 'PyQt6.QtNetwork',
    'PyQt6.QtBluetooth', 'PyQt6.QtNfc', 'PyQt6.QtPositioning', 'PyQt6.QtSensors',
    'PyQt6.QtSerialPort', 'PyQt6.QtWebSockets', 'PyQt6.QtWebChannel',
    'PyQt6.QtDesigner', 'PyQt6.QtHelp', 'PyQt6.QtUiTools',
    'PyQt6.QtPdf', 'PyQt6.QtPdfWidgets',
    'PyQt6.QtCharts', 'PyQt6.QtDataVisualization', 'PyQt6.Qt3DCore',
    'PyQt6.QtSvg', 'PyQt6.QtSvgWidgets',
    'tkinter', 'unittest',
]

# --exclude-module alone only drops the Python bindings; the Qt DLLs and their
# plugins still get collected. Drop those binaries too (the only Qt DLLs the app
# needs are Qt6Core / Qt6Gui / Qt6Widgets plus the platform plugin).
DROP_BINARIES = ('Qt6Pdf', 'Qt6Svg', 'Qt6Network',
                 'qpdf.dll', 'qsvg.dll', 'qsvgicon.dll',
                 'qopensslbackend.dll', 'qschannelbackend.dll',
                 'qnetworklistmanager.dll')

a = Analysis(
    ['D:/数据整理/TC/customer_number_processor_single.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
a.binaries = [entry for entry in a.binaries
              if not any(token in os.path.basename(entry[0]) for token in DROP_BINARIES)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='号码工作台',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
