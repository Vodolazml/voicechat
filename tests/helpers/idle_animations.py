"""Loads the real interface offscreen, puts it in a call, and prints the QML animations still running
(as JSON). Any running animation makes Qt redraw every shown window of the app at full speed."""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, sys.argv[1])

from PySide6.QtCore import QObject, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.client.ui import app as appmod  # noqa: E402
from app.client.ui.backend import Backend  # noqa: E402
from app.client.ui.screen import VideoFeed  # noqa: E402

qa = QApplication(sys.argv[:1])
qmlRegisterType(VideoFeed, "VoiceChat", 1, 0, "VideoFeed")
backend = Backend()
engine = QQmlApplicationEngine()
ctx = engine.rootContext()
ctx.setContextProperty("backend", backend)
ctx.setContextProperty("overlayHelper", appmod.OverlayHelper())
ctx.setContextProperty("trayAvailable", False)
ctx.setContextProperty("startHidden", False)
engine.load(QUrl.fromLocalFile(str(appmod.QML_DIR / "Main.qml")))


def in_call() -> None:
    backend._me = {"id": 1, "name": "me", "username": "me"}
    backend._set("_view", "main", backend.viewChanged)
    backend._call_channel = 4
    backend.callChanged.emit()


def report() -> None:
    running = []
    for window in QGuiApplication.topLevelWindows():
        for obj in window.findChildren(QObject):
            name = obj.metaObject().className()
            if ("Animation" in name or "Animator" in name) and obj.property("running"):
                parent = obj.parent()
                running.append(f"{name} on {parent.metaObject().className() if parent else '?'} in {window.title()!r}")
    print("RESULT " + json.dumps(running))
    qa.quit()


QTimer.singleShot(300, in_call)
QTimer.singleShot(2000, report)
qa.exec()
