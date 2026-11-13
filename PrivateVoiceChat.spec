# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

datas = []
binaries = []
hiddenimports = []
# No collect_all('PySide6'): it took every Qt module (WebEngine alone is well over 100 MB), and filtering
# its list was not enough, because the QML hook and the DLL dependency scan pulled the same files back in.
# PyInstaller's own PySide6 hooks bring in what the imported modules need; the filter below then runs on
# the final file list. The app uses Core, Gui, Widgets, Network, Qml, Quick (Controls, Layouts, Shapes,
# Dialogs, Window) and Multimedia.
_UNUSED_QT = ('WebEngine', 'WebView', 'WebChannel', 'WebSockets', 'Qt3D', 'Qt63D', 'Quick3D', 'Pdf', 'Designer',
              'Charts', 'DataVisualization', 'Graphs', 'Location', 'Positioning', 'Bluetooth', 'Nfc',
              'SerialPort', 'SerialBus', 'Sensors', 'RemoteObjects', 'Scxml', 'StateMachine', 'VirtualKeyboard',
              'Lottie', 'TextToSpeech', 'HttpServer', 'SpatialAudio', 'Help', 'UiTools', 'NetworkAuth',
              'QtTest', 'QuickTest', 'Sql', 'QmlLocalStorage',
              'QmlXmlListModel', 'CanvasPainter', 'Wayland', 'EglFS', 'assistant', 'designer',
              'linguist', 'lupdate', 'lrelease', 'qmlls', 'qmlformat', 'qmllint', 'qmlcachegen', 'qmltyperegistrar',
              'balsam', 'svgtoqml', 'lottietoqml')


def _qt_used(entry) -> bool:
    path = str(entry[0]).replace('\\', '/')
    name = path.rsplit('/', 1)[-1]
    if '/translations/' in path and not ('qt_ru' in name or 'qtbase_ru' in name or 'qtmultimedia_ru' in name
                                         or 'qtdeclarative_ru' in name):
        return False
    return not any(part in path for part in _UNUSED_QT)


hiddenimports += ['PySide6.' + m for m in ('QtCore', 'QtGui', 'QtWidgets', 'QtNetwork', 'QtQml', 'QtQuick',
                                            'QtQuickControls2', 'QtMultimedia', 'QtOpenGL', 'QtSvg')]
tmp_ret = collect_all('sounddevice')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

tmp_ret = collect_all('livekit.rtc')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

datas += [('assets/app_icon.ico', 'assets'), ('app/client/ui/qml', 'app/client/ui/qml')]
import os
if os.path.isdir('assets/voice'):
    datas += [('assets/voice', 'assets/voice')]  # spoken mic/sound prompts
if os.path.isfile('THIRD_PARTY_NOTICES.md'):
    datas += [('THIRD_PARTY_NOTICES.md', '.')]  # licenses of the bundled packages (deploy/third_party_notices.py)

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
      StringStruct('CompanyName', 'Private VoiceChat Team'),
      StringStruct('FileDescription', 'VoiceChat - voice chat and screen sharing'),
      StringStruct('FileVersion', '{_version}'),
      StringStruct('InternalName', 'PrivateVoiceChat'),
      StringStruct('LegalCopyright', '© 2026 Private VoiceChat Team'),
      StringStruct('OriginalFilename', 'PrivateVoiceChat.exe'),
      StringStruct('ProductName', 'VoiceChat'),
      StringStruct('ProductVersion', '{_version}')])]),
    VarFileInfo([VarStruct('Translation', [0x0419, 1200])])
  ]
)
""")
binaries += collect_dynamic_libs('pyrnnoise', destdir='pyrnnoise')
# PyAV with its ffmpeg: screen shares encoded by an NVIDIA card (h264_nvenc). Its DLLs live next to the
# package in "av.libs" (delvewheel); take them all, avcodec needs every one to load.
try:
    import glob as _glob
    import av as _av
    tmp_ret = collect_all('av')
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    _av_libs = os.path.join(os.path.dirname(os.path.dirname(_av.__file__)), 'av.libs')
    binaries += [(path, 'av.libs') for path in _glob.glob(os.path.join(_av_libs, '*.dll'))]
except Exception as _exc:
    print('PyAV not bundled:', _exc)
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
    excludes=['PySide6.' + m for m in ('QtWebEngineCore', 'QtWebEngineWidgets', 'QtWebEngineQuick', 'Qt3DCore',
              'QtPdf', 'QtCharts', 'QtDataVisualization', 'QtDesigner', 'QtQuick3D', 'QtGraphs', 'QtLocation',
              'QtBluetooth', 'QtSensors', 'QtSql', 'QtTest', 'QtHelp')],
    noarchive=False,
    optimize=0,
)
# Drop unused Qt from the final list, whichever hook or dependency scan brought it in.
_before = len(a.binaries) + len(a.datas)
a.binaries = [b for b in a.binaries if _qt_used(b)]
a.datas = [d for d in a.datas if _qt_used(d)]
print(f'Qt filter removed {_before - len(a.binaries) - len(a.datas)} files')
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
