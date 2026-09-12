# -*- mode: python ; coding: utf-8 -*-

# The GUI imports the audit stages lazily (inside button handlers) so the window
# appears instantly, and `config` is a namespace package. Both patterns hide
# modules from PyInstaller's static analysis, so they are listed explicitly.
hiddenimports = [
    'auditCore',
    'browser',
    'configStore',
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

a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=[],
    datas=[('config', 'config')],
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
