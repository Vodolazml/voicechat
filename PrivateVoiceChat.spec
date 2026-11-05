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
import os
if os.path.isdir('assets/voice'):
    datas += [('assets/voice', 'assets/voice')]  # spoken mic/sound prompts

# Windows version resource, generated from app/version.py so it never goes stale (build/ is not in git).
import re as _re
_version = _re.search(r'"([^"]+)"', open('app/version.py', encoding='utf-8').read()).group(1)
_nums = tuple((list(map(int, _re.findall(r'\d+', _version)))[:4] + [0, 0, 0, 0])[:4])
os.makedirs('build', exist_ok=True)
with open('build/version_info.py', 'w', encoding='utf-8') as _f:
    _f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={_nums}, prodvers={_nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041904B0', [
      StringStruct('CompanyName', 'Private VoiceChat'),
      StringStruct('FileDescription', 'VoiceChat - voice chat and screen sharing'),
      StringStruct('FileVersion', '{_version}'),
      StringStruct('InternalName', 'PrivateVoiceChat'),
      StringStruct('LegalCopyright', 'Private VoiceChat'),
      StringStruct('OriginalFilename', 'PrivateVoiceChat.exe'),
      StringStruct('ProductName', 'VoiceChat'),
      StringStruct('ProductVersion', '{_version}')])]),
    VarFileInfo([VarStruct('Translation', [0x0419, 1200])])
  ]
)
""")
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
