"""Keeps a single running instance; a second launch just raises the first one.

Without this, opening the app again from the Start Menu/desktop shortcut
while it is already minimized to the tray would start a whole second
process - a second voice/audio session fighting the first one for the
microphone and speakers.

A first instance that has hung (it once sat frozen for 40 minutes at start-up while every new launch only
pinged it) does not answer the ping. The new launch then ends it and starts normally.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger("voicechat.ui")

SERVER_NAME = "PrivateVoiceChat-single-instance"
_PING = b"show"
_ACK = b"ok"
ANSWER_TIMEOUT_SECONDS = 6.0


def _pid_file() -> Path:
    from app.client.settings_store import SETTINGS_PATH

    return SETTINGS_PATH.parent / "instance.pid"


class SingleInstanceGuard(QObject):
    show_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._server: QLocalServer | None = None
        self._outgoing: QLocalSocket | None = None

    def try_acquire(self) -> bool:
        """Returns True if this process should proceed as the primary instance."""
        socket = QLocalSocket()
        socket.connectToServer(SERVER_NAME)
        if socket.waitForConnected(200):
            socket.write(_PING)
            socket.waitForBytesWritten(200)
            if self._answered(socket) or not self._end_hung_instance():
                socket.disconnectFromServer()
                # Kept alive deliberately: a local variable would be garbage
                # collected as soon as this function returns, which can tear
                # down the pipe before the write is actually delivered.
                self._outgoing = socket
                return False
            socket.abort()
        QLocalServer.removeServer(SERVER_NAME)  # drop a stale pipe left by a crash
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._server.listen(SERVER_NAME)
        try:
            path = _pid_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(os.getpid()), encoding="ascii")
        except OSError:
            pass
        return True

    @staticmethod
    def _answered(socket: QLocalSocket) -> bool:
        deadline = time.monotonic() + ANSWER_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            # Events are pumped so a first instance in this same process (tests) can answer too.
            QCoreApplication.processEvents()
            if socket.bytesAvailable() or socket.waitForReadyRead(50):
                return True
            if socket.state() != QLocalSocket.ConnectedState:
                # Versions before 0.4.5 close without answering; they were alive if they read the ping.
                return True
        return False

    @staticmethod
    def _end_hung_instance() -> bool:
        """Ends the frozen first instance. False when that is not possible or not safe."""
        try:
            pid = int(_pid_file().read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return False
        if pid == os.getpid() or sys.platform != "win32":
            return False
        if not _terminate_if_same_app(pid):
            return False
        logger.warning("The running VoiceChat (pid %s) did not respond: ended it to start again", pid)
        return True

    def _on_new_connection(self) -> None:
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        socket.readyRead.connect(lambda: self._consume(socket))
        socket.disconnected.connect(socket.deleteLater)

    def _consume(self, socket: QLocalSocket) -> None:
        socket.readAll()
        socket.write(_ACK)
        socket.flush()
        self.show_requested.emit()


def _terminate_if_same_app(pid: int) -> bool:
    """TerminateProcess, but only if that pid is still this very program (pids get reused)."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_TERMINATE, PROCESS_QUERY_LIMITED_INFORMATION, SYNCHRONIZE = 0x0001, 0x1000, 0x00100000
    handle = kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return False
        if os.path.normcase(os.path.abspath(buffer.value)) != os.path.normcase(os.path.abspath(sys.executable)):
            return False
        if not kernel32.TerminateProcess(handle, 1):
            return False
        kernel32.WaitForSingleObject(handle, 5000)
        return True
    finally:
        kernel32.CloseHandle(handle)
