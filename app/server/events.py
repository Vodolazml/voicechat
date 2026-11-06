"""In-process event hub: pushes presence, channel and chat changes to connected clients over /events.

Endpoints run in a thread pool, so publishing is thread-safe and never blocks: each subscriber has a
bounded queue, and a client that cannot keep up is disconnected (it reconnects and resyncs).
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterable
from typing import Any

QUEUE_SIZE = 256


class Subscriber:
    def __init__(self, user_id: int, loop: asyncio.AbstractEventLoop) -> None:
        self.user_id = user_id
        self.loop = loop
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self.overflowed = False

    def offer(self, event: dict[str, Any] | None) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.overflowed = True
            # Wake the sender so it notices the overflow and closes the socket.
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(None)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass


class EventHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[int, set[Subscriber]] = {}

    def subscribe(self, user_id: int) -> Subscriber:
        subscriber = Subscriber(user_id, asyncio.get_running_loop())
        with self._lock:
            self._subscribers.setdefault(user_id, set()).add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            group = self._subscribers.get(subscriber.user_id)
            if group:
                group.discard(subscriber)
                if not group:
                    del self._subscribers[subscriber.user_id]

    def online_user_ids(self) -> set[int]:
        with self._lock:
            return set(self._subscribers)

    def publish(self, user_ids: Iterable[int], event: dict[str, Any]) -> None:
        with self._lock:
            targets = [sub for uid in set(user_ids) for sub in self._subscribers.get(uid, ())]
        for subscriber in targets:
            try:
                subscriber.loop.call_soon_threadsafe(subscriber.offer, event)
            except RuntimeError:
                # Loop already closed (shutdown); nothing to deliver to.
                pass

    def disconnect_user(self, user_id: int) -> None:
        """Close every event stream of a user (sign-out, disable)."""
        with self._lock:
            targets = list(self._subscribers.get(user_id, ()))
        for subscriber in targets:
            try:
                subscriber.loop.call_soon_threadsafe(subscriber.offer, {"type": "session.ended"})
            except RuntimeError:
                pass


hub = EventHub()
