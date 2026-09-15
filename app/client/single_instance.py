"""Keeps a single running instance; a second launch just raises the first one.

Without this, opening the app again from the Start Menu/desktop shortcut
while it is already minimized to the tray would start a whole second
process - a second voice/audio session fighting the first one for the
microphone and speakers.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = "PrivateVoiceChat-single-instance"
_PING = b"show"


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
            socket.disconnectFromServer()
            # Kept alive deliberately: a local variable would be garbage
            # collected as soon as this function returns, which can tear
            # down the pipe before the write is actually delivered.
            self._outgoing = socket
            return False
        QLocalServer.removeServer(SERVER_NAME)  # drop a stale pipe left by a crash
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._server.listen(SERVER_NAME)
        return True

    def _on_new_connection(self) -> None:
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        socket.readyRead.connect(lambda: self._consume(socket))
        socket.disconnected.connect(socket.deleteLater)

    def _consume(self, socket: QLocalSocket) -> None:
        socket.readAll()
        self.show_requested.emit()
