"""Event persistence and query (member 4).

EventBus callbacks must stay non-blocking: events are queued and written by a background task.
Database I/O runs in a worker thread so the asyncio loop is not blocked.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

MAX_QUERY_LIMIT = 200
DEFAULT_QUERY_LIMIT = 50

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    name TEXT NOT NULL,
    device_id TEXT,
    request_id TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_request ON events(request_id);
CREATE INDEX IF NOT EXISTS idx_events_device ON events(device_id);
CREATE INDEX IF NOT EXISTS idx_events_name ON events(name);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""

# Events we always persist; others are ignored to keep the DB focused.
_PERSIST_NAMES = frozenset({
    "device.connected",
    "device.disconnected",
    "device.offline",
    "device.heartbeat",
    "device.state",
    "command.submitted",
    "command.sent",
    "command.timeout_retry",
    "command.completed",
    "udp.rejected",
})


def parse_query_limit(raw: str | None) -> int | None:
    """Return a limit in 1..MAX_QUERY_LIMIT, or None if the parameter is invalid."""
    if raw is None:
        return DEFAULT_QUERY_LIMIT
    try:
        value = int(raw)
    except ValueError:
        return None
    if value < 1 or value > MAX_QUERY_LIMIT:
        return None
    return value


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    data = json.loads(row["payload"])
    event: dict[str, Any] = {
        "id": row["id"],
        "event": row["name"],
        "timestamp": row["ts"],
        **data,
    }
    if row["device_id"]:
        event.setdefault("device_id", row["device_id"])
    if row["request_id"]:
        event.setdefault("request_id", row["request_id"])
    return event


class EventLogStore:
    def __init__(self, path: str | Path = "data/events.sqlite"):
        self.path = Path(path)
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._conn: sqlite3.Connection | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="event-log")

    def enqueue(self, event: dict) -> None:
        name = event.get("event")
        if name not in _PERSIST_NAMES:
            return
        self._queue.put_nowait(event)

    def subscribe_callback(self, event: dict) -> None:
        self.enqueue(event)

    def _open_writer(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.executescript(_SCHEMA)
        conn.commit()
        return conn

    def _insert(self, event: dict) -> None:
        if self._conn is None:
            raise RuntimeError("log writer not initialized")
        name = event["event"]
        ts = event.get("timestamp", "")
        device_id = event.get("device_id")
        request_id = event.get("request_id")
        payload = {k: v for k, v in event.items() if k not in ("event", "timestamp")}
        self._conn.execute(
            "INSERT INTO events (ts, name, device_id, request_id, payload) VALUES (?, ?, ?, ?, ?)",
            (ts, name, device_id, request_id, json.dumps(payload, ensure_ascii=False)),
        )
        self._conn.commit()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._conn = await asyncio.get_running_loop().run_in_executor(self._executor, self._open_writer)
        self._task = asyncio.create_task(self._writer_loop())

    async def drain_and_close(self, timeout: float = 5.0) -> None:
        """Wait for queued events to flush, then stop the background writer."""
        if self._task is None:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        idle_rounds = 0
        while loop.time() < deadline:
            if self._queue.empty():
                idle_rounds += 1
                if idle_rounds >= 3:
                    break
                await asyncio.sleep(0.02)
            else:
                idle_rounds = 0
                await asyncio.sleep(0.01)
        await self._queue.put(None)
        await self._task
        self._task = None

    async def close(self) -> None:
        await self.drain_and_close()

    async def _writer_loop(self) -> None:
        try:
            while True:
                event = await self._queue.get()
                if event is None:
                    break
                try:
                    await asyncio.get_running_loop().run_in_executor(self._executor, self._insert, event)
                except Exception:
                    LOG.exception("failed to persist event %s", event.get("event"))
        finally:
            if self._conn:
                await asyncio.get_running_loop().run_in_executor(self._executor, self._conn.close)
                self._conn = None

    def _read(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_event(row) for row in rows]
        finally:
            conn.close()

    def query_recent(
        self,
        *,
        limit: int = DEFAULT_QUERY_LIMIT,
        event_name: str | None = None,
        device_id: str | None = None,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), MAX_QUERY_LIMIT))
        clauses = ["1=1"]
        params: list[Any] = []
        if event_name:
            clauses.append("name = ?")
            params.append(event_name)
        if device_id:
            clauses.append("device_id = ?")
            params.append(device_id)
        params.append(limit)
        sql = f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?"
        return list(reversed(self._read(sql, tuple(params))))

    def query_by_request_id(self, request_id: str) -> list[dict[str, Any]]:
        return self._read(
            "SELECT * FROM events WHERE request_id = ? ORDER BY id ASC",
            (request_id,),
        )

    def query_command_trail(self, request_id: str) -> list[dict[str, Any]]:
        """All persisted rows for one control flow, in time order."""
        return self.query_by_request_id(request_id)

    def count_events(self) -> int:
        conn = sqlite3.connect(self.path)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        finally:
            conn.close()


def attach_event_log(gateway, store: EventLogStore) -> EventLogStore:
    gateway.events.subscribe(store.subscribe_callback)
    gateway.log_store = store  # type: ignore[attr-defined]
    return store
