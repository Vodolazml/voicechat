# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all('PySide6')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('sounddevice')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

tmp_ret = collect_all('livekit.rtc')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

datas += [('assets/app_icon.ico', 'assets'), ('app/client/ui/qml', 'app/client/ui/qml')]
binaries += collect_dynamic_libs('pyrnnoise', destdir='pyrnnoise')
hiddenimports += ['mss', 'websockets', 'websockets.asyncio.client', 'app.client.ui.app']
try:
    tmp_ret = collect_all('dxcam')  # Windows only; optional
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2] + ['comtypes', 'comtypes.stream']
except Exception:
    pass


a = Analysis(
    ['run_client.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
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
    [],
    exclude_binaries=True,
    name='PrivateVoiceChat',
    icon='assets/app_icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # No UPX and a proper version resource: packed, anonymous exes are what antivirus heuristics flag.
    upx=False,
    version='build/version_info.py',
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='PrivateVoiceChat',
)
