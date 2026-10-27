"""Event seam for member 3 (live updates) and member 4 (persistence).

Callbacks run synchronously: enqueue work rather than performing disk/network I/O.
"""

from datetime import datetime, timezone
import logging
from typing import Callable

LOG = logging.getLogger(__name__)


class EventBus:
    def __init__(self):
        self._subscribers: list[Callable[[dict], None]] = []

    def subscribe(self, callback: Callable[[dict], None]) -> None:
        self._subscribers.append(callback)

    def emit(self, name: str, **data) -> None:
        event = {"event": name, "timestamp": datetime.now(timezone.utc).isoformat(), **data}
        for callback in self._subscribers:
            try:
                callback(event)
            except Exception:
                LOG.exception("event subscriber failed")
