"""In-memory, online-only registry; extend lifecycle rules in member 2's module."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DeviceSession:
    device_id: str
    device_type: str
    writer: asyncio.StreamWriter
    state: dict
    last_seen: str = field(default_factory=utc_now)
    command_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def snapshot(self) -> dict:
        return {"device_id": self.device_id, "device_type": self.device_type,
                "protocol": "TCP", "online": True, "state": dict(self.state),
                "last_seen": self.last_seen}


class DeviceRegistry:
    def __init__(self):
        self._devices: dict[str, DeviceSession] = {}

    def register(self, session: DeviceSession) -> None:
        if session.device_id in self._devices:
            raise ValueError("device_id already connected")
        self._devices[session.device_id] = session

    def get(self, device_id: str) -> DeviceSession | None:
        return self._devices.get(device_id)

    def remove(self, session: DeviceSession) -> bool:
        if self._devices.get(session.device_id) is session:
            del self._devices[session.device_id]
            return True
        return False

    def list_devices(self) -> list[dict]:
        return [self._devices[key].snapshot() for key in sorted(self._devices)]
