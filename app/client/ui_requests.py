import threading

from PySide6.QtCore import QObject, Signal


class UiRequests(QObject):
    voice_ready = Signal(int, object)
    ping_ready = Signal(object)
    message = Signal(str)

    def __init__(self, api):
        super().__init__()
        self.api = api
        self._pending = set()
        self._lock = threading.Lock()
        self.closed = False

    def submit(self, key, operation, completed):
        with self._lock:
            if key in self._pending or self.closed:
                return
            self._pending.add(key)
        def run():
            try:
                try:
                    result = operation()
                except Exception as exc:
                    result = exc
                if not self.closed:
                    completed(result)
            finally:
                with self._lock:
                    self._pending.discard(key)
        threading.Thread(target=run, daemon=True, name='ui-request').start()

    def voice(self, channel_id):
        self.submit(('voice', channel_id), lambda: self.api.voice_states(channel_id),
                    lambda result: self.voice_ready.emit(channel_id, result))

    def ping(self):
        self.submit('ping', self.api.ping_ms, self.ping_ready.emit)
