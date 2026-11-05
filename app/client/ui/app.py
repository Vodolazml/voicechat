"""Entry point of the desktop app (QML interface)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Slot
from PySide6.QtGui import QAction, QFont, QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from app.client.single_instance import SingleInstanceGuard

from . import platform as native
from .backend import Backend, configure_logging
from .screen import VideoView

QML_DIR = Path(__file__).resolve().parent / "qml"


def _resource(path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
    return base / path


class OverlayHelper(QObject):
    @Slot(QObject)
    def clickThrough(self, window) -> None:
        native.make_click_through(int(window.winId()))


def _tray(app: QApplication, backend: Backend, icon: QIcon) -> QSystemTrayIcon | None:
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("VoiceChat")
    menu = QMenu()
    show = QAction("Открыть VoiceChat", menu)
    mute = QAction("Микрофон", menu, checkable=True)
    deafen = QAction("Звук", menu, checkable=True)
    leave = QAction("Выйти из канала", menu)
    quit_action = QAction("Закрыть приложение", menu)
    show.triggered.connect(backend.requestShow)
    mute.triggered.connect(backend.toggleMute)
    deafen.triggered.connect(backend.toggleDeafen)
    leave.triggered.connect(backend.leaveCall)
    quit_action.triggered.connect(app.quit)
    for action in (show, None, mute, deafen, leave, None, quit_action):
        menu.addSeparator() if action is None else menu.addAction(action)

    def sync() -> None:
        mute.setChecked(not backend.muted)
        deafen.setChecked(not backend.deafened)
        mute.setEnabled(backend.inCall)
        deafen.setEnabled(backend.inCall)
        leave.setEnabled(backend.inCall)
        tray.setToolTip(f"VoiceChat · {backend.callChannelName}" if backend.inCall else "VoiceChat")
    backend.localChanged.connect(sync)
    backend.callChanged.connect(sync)
    sync()
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: backend.requestShow()
                           if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
    tray._menu = menu  # keep the menu alive
    tray.show()
    return tray


def main() -> int:
    configure_logging()
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("VoiceChat")
    app.setOrganizationName("PrivateVoiceChat")
    app.setQuitOnLastWindowClosed(False)
    icon = QIcon(str(_resource("assets/app_icon.ico")))
    app.setWindowIcon(icon)
    app.setFont(QFont("Segoe UI Variable Text" if sys.platform == "win32" else "Inter", 10))

    guard = SingleInstanceGuard()
    if not guard.try_acquire():
        return 0

    qmlRegisterType(VideoView, "VoiceChat", 1, 0, "VideoView")
    backend = Backend()
    helper = OverlayHelper()
    tray = _tray(app, backend, icon)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("overlayHelper", helper)
    engine.rootContext().setContextProperty("trayAvailable", tray is not None)
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    if not engine.rootObjects():
        return 1
    window = engine.rootObjects()[0]
    if "--tray" in sys.argv and tray is not None:
        window.hide()
    guard.show_requested.connect(backend.requestShow)

    if tray is None:
        app.setQuitOnLastWindowClosed(True)
    app.aboutToQuit.connect(backend.shutdown)
    code = app.exec()
    del engine  # QML goes first, so bindings never see a destroyed backend
    return code


if __name__ == "__main__":
    raise SystemExit(main())
