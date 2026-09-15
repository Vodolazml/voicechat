from __future__ import annotations

import threading
from time import monotonic


class ConnectionMaintenance:
    """Keep the active call alive independently of Qt timers and selected views."""

    def __init__(self, api, channel_id, flags, sync_keys, status):
        self.api, self.channel_id = api, channel_id
        self.flags, self.sync_keys, self.status = flags, sync_keys, status
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="call-maintenance", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=20)
        if self._thread.is_alive():
            raise RuntimeError("Не удалось завершить обслуживание соединения")

    def _run(self):
        renewed = monotonic()
        while not self._stop.is_set():
            try:
                if monotonic() - renewed > 600:
                    data = self.api.request("POST", "/auth/renew")
                    self.api.token = data["access_token"]
                    renewed = monotonic()
                muted, deafened, speaking = self.flags()
                try:
                    self.api.update_voice(self.channel_id, muted, deafened, speaking)
                except Exception as exc:
                    if getattr(exc, "status_code", None) != 404 or self._stop.is_set():
                        raise
                    self.api.connect(self.channel_id, muted, deafened)
                if not self._stop.is_set():
                    self.sync_keys()
            except Exception as exc:
                self.status(f"Соединение: {exc}")
            self._stop.wait(3)
