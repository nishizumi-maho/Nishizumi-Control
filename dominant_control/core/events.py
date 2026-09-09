"""Small thread-safe event bus used by feature packages."""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._listeners: dict[str, list[Callable[..., Any]]] = defaultdict(list)

    def subscribe(self, event: str, callback: Callable[..., Any]) -> Callable[[], None]:
        with self._lock:
            self._listeners[event].append(callback)

        def unsubscribe() -> None:
            with self._lock:
                listeners = self._listeners.get(event, [])
                if callback in listeners:
                    listeners.remove(callback)

        return unsubscribe

    def publish(self, event: str, **payload: Any) -> None:
        with self._lock:
            listeners = tuple(self._listeners.get(event, ()))
        for callback in listeners:
            try:
                callback(**payload)
            except Exception:
                continue
