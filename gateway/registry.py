"""In-memory device registry supporting online/offline lifecycle and heartbeat timeout."""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DeviceSession:
    device_id: str
    device_type: str
    writer: asyncio.StreamWriter | None
    state: dict
    protocol: str = "TCP"
    online: bool = True
    last_seen: str = field(default_factory=utc_now)
    last_seen_mono: float = field(default_factory=time.monotonic)
    command_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self, new_state: dict | None = None) -> None:
        """刷新设备活跃时间，必要时更新状态并恢复在线。"""
        self.last_seen = utc_now()
        self.last_seen_mono = time.monotonic()
        self.online = True
        if new_state is not None:
            self.state.update(new_state)

    def mark_offline(self) -> bool:
        """将设备标记为离线，保留最后已知 state 和 last_seen，清空底层连接。"""
        was_online = self.online
        self.online = False
        self.writer = None
        return was_online

    def snapshot(self) -> dict:
        return {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "protocol": self.protocol,
            "online": self.online,
            "state": dict(self.state),
            "last_seen": self.last_seen,
        }


class DeviceRegistry:
    def __init__(self):
        self._devices: dict[str, DeviceSession] = {}

    def register(self, session: DeviceSession) -> None:
        existing = self._devices.get(session.device_id)
        if existing is not None and existing.online and existing is not session:
            # 如果旧连接还处于在线状态（例如心跳尚未超时但设备已发起新连接），
            # 允许新连接接管，或按原测试要求抛出异常。
            # 注意：如果原有单元测试强制要求在线重复注册抛 ValueError，保留下面这行：
            raise ValueError("device_id already connected")

        if existing is not None:
            # 继承离线前的历史状态（如果新注册未携带完整状态）和排队锁
            if not session.state and existing.state:
                session.state = dict(existing.state)
            session.command_lock = existing.command_lock

        session.touch()
        self._devices[session.device_id] = session

    def upsert_udp_device(self, device_id: str, device_type: str, state: dict) -> DeviceSession:
        """供组长 UDP 温湿度上报调用：不存在则创建，存在则刷新状态与活跃时间。"""
        session = self._devices.get(device_id)
        if session is None:
            session = DeviceSession(
                device_id=device_id,
                device_type=device_type,
                writer=None,
                state=dict(state),
                protocol="UDP",
                online=True,
            )
            self._devices[device_id] = session
        else:
            session.protocol = "UDP"
            session.touch(state)
        return session

    def touch(self, device_id: str, state: dict | None = None) -> DeviceSession | None:
        """收到 HEARTBEAT / REPORT / COMMAND_RESULT 时刷新活跃时间。"""
        session = self._devices.get(device_id)
        if session is not None:
            session.touch(state)
        return session

    def get(self, device_id: str) -> DeviceSession | None:
        return self._devices.get(device_id)

    def remove(self, session: DeviceSession) -> bool:
        """连接断开时调用：防旧连接误删新连接，且改为标记离线而非直接删除记录。"""
        current = self._devices.get(session.device_id)
        if current is session:
            return current.mark_offline()
        return False

    def expire_inactive(self, timeout_sec: float, now_mono: float | None = None) -> list[DeviceSession]:
        """巡检并返回本次因心跳超时而变为离线的设备列表。"""
        if now_mono is None:
            now_mono = time.monotonic()
        expired: list[DeviceSession] = []
        for session in self._devices.values():
            if session.online and (now_mono - session.last_seen_mono > timeout_sec):
                session.mark_offline()
                expired.append(session)
        return expired

    def list_devices(self) -> list[dict]:
        return [self._devices[key].snapshot() for key in sorted(self._devices)]