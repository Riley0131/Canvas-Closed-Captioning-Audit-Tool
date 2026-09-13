# -*- mode: python ; coding: utf-8 -*-
import sys

# gui.py and gui_api.py import the audit pipeline at module level (no more
# lazy per-button imports now that startup doesn't block on Tk), so
# PyInstaller's own static analysis reaches most of it. `config` is a
# namespace package (no __init__.py), which trips up that analysis, so its
# modules are listed explicitly along with it.
hiddenimports = [
    'auditCore',
    'browser',
    'configStore',
    'gui_api',
    'config.canvasAPI',
    'config.panoptoKey',
    'config.version',
    'dataReset',
    'individualAudit',
    'panoptoCaptions',
    'panoptoVideo',
    'pullModules',
    'runAudit',
    'sortEmbeddedVideos',
    'youtubeVideo',
]

# pywebview picks its backend at runtime via importlib based on the host
# platform (webview/platforms.py), which static analysis cannot see - list
# the backend for whichever platform this spec is built on. Build on each
# target platform separately; a hiddenimport for the wrong platform's
# backend is harmless (its own imports just fail at PyInstaller analysis
# time as "not found" and are skipped) but only the matching one is needed.
if sys.platform == 'darwin':
    hiddenimports += ['webview.platforms.cocoa']
elif sys.platform.startswith('win'):
    hiddenimports += ['webview.platforms.winforms', 'webview.platforms.edgechromium']
else:
    hiddenimports += ['webview.platforms.gtk', 'webview.platforms.qt']

a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=[],
    # webui/ (the HTML/CSS/JS the window renders) must ship alongside the
    # executable; gui.py.resolveBaseDir() finds it via sys._MEIPASS at
    # runtime when frozen.
    datas=[('config', 'config'), ('webui', 'webui')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='CC-Auditor',
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
)
