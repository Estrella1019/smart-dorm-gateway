"""Runnable TCP bootstrap. Modified for member 2 (queuing, retries, soft-offline)."""

import argparse
import asyncio
import json
import logging
from uuid import uuid4

from shared.config import Settings, load_settings
from shared.protocol import ProtocolError, close_writer, make_message, read_message, send_message
from gateway.events import EventBus
from gateway.registry import DeviceRegistry, DeviceSession
from gateway.storage import EventLogStore, attach_event_log
from gateway.udp import SensorReceiver

LOG = logging.getLogger(__name__)


class Gateway:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.events = EventBus()
        self.registry = DeviceRegistry()
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.Task] = set()
        self._pending: dict[str, tuple[DeviceSession, asyncio.Future]] = {}
        self._monitor_task: asyncio.Task | None = None

        # 组员 2：可靠控制配置
        self.max_retries = 2
        self.heartbeat_timeout = self.settings.heartbeat_timeout  # 心跳超时时间 (秒)

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, self.settings.host, self.settings.tcp_port,
            limit=131072,
        )
        try:
            self._udp, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: SensorReceiver(self), local_addr=(self.settings.host, self.settings.udp_port))
        except BaseException:
            await self.close()
            raise
        self._monitor_task = asyncio.create_task(self._monitor_heartbeats())

    @property
    def address(self) -> tuple:
        if self._server is None:
            raise RuntimeError("gateway not started")
        return self._server.sockets[0].getsockname()

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("gateway not started")
        await self._server.serve_forever()

    async def close(self) -> None:
        if self._monitor_task:
            self._monitor_task.cancel()
        if getattr(self, "_udp", None):
            self._udp.close()
        if self._server:
            self._server.close()
        tasks = list(self._clients)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._monitor_task:
            await asyncio.gather(self._monitor_task, return_exceptions=True)
        if self._server:
            await self._server.wait_closed()

    def list_devices(self) -> list[dict]:
        return self.registry.list_devices()

    async def _monitor_heartbeats(self) -> None:
        """后台任务：定时巡检心跳超时的设备，将其标记为软离线。"""
        try:
            while True:
                await asyncio.sleep(2.0)
                expired = self.registry.expire_inactive(self.heartbeat_timeout)
                for session in expired:
                    self.events.emit("device.offline", device_id=session.device_id, reason="heartbeat_timeout")
                    # 取消该设备正在等待的指令
                    for req_id, (owner, future) in list(self._pending.items()):
                        if owner is session and not future.done():
                            future.set_result({"status": "unknown", "reason": "device_disconnected"})
        except asyncio.CancelledError:
            pass

    async def send_to_device(self, device_id: str, message: dict) -> None:
        session = self.registry.get(device_id)
        if session is None or not session.online or session.writer is None:
            raise ConnectionError("device offline")
        await send_message(session.writer, message)

    async def submit_command(self, device_id: str, action: str, value: bool) -> dict:
        if not isinstance(action, str) or action not in {"set_power", "set_locked"} or type(value) is not bool:
            raise ProtocolError("action must be set_power/set_locked with a boolean value")

        request_id = uuid4().hex
        session = self.registry.get(device_id)

        if session is None or not session.online:
            return {"request_id": request_id, "status": "failed", "reason": "device_offline"}

        expected = {"light": "set_power", "door_lock": "set_locked"}.get(session.device_type)
        if action != expected:
            raise ProtocolError("action does not match device type")

        # 组员 2 改造：利用 command_lock 实现同设备串行排队
        async with session.command_lock:
            # 再次检查设备是否在线（防止排队期间掉线）
            if not session.online or session.writer is None:
                return {"request_id": request_id, "status": "failed", "reason": "device_offline"}

            future = asyncio.get_running_loop().create_future()
            self._pending[request_id] = (session, future)
            self.events.emit("command.submitted", device_id=device_id, request_id=request_id)

            cmd_msg = make_message(
                "COMMAND", {"action": action, "value": value},
                device_id=device_id, request_id=request_id
            )

            result = {"status": "unknown", "reason": "confirmation_timeout"}

            try:
                # 组员 2 改造：使用相同的 request_id 进行有限重试
                for attempt in range(1, self.max_retries + 2):
                    if not session.online or session.writer is None:
                        result = {"status": "unknown", "reason": "device_disconnected"}
                        break

                    try:
                        await send_message(session.writer, cmd_msg)
                        self.events.emit("command.sent", device_id=device_id, request_id=request_id, attempt=attempt)
                    except (OSError, ConnectionError):
                        result = {"status": "unknown", "reason": "device_disconnected"}
                        break

                    try:
                        # shield 防止超时抛错取消掉 future，以便下一轮重试能继续用它接收迟到的结果
                        result = await asyncio.wait_for(asyncio.shield(future), self.settings.command_timeout)
                        break  # 成功收到回复，跳出重试循环
                    except TimeoutError:
                        self.events.emit("command.timeout_retry", device_id=device_id, request_id=request_id, attempt=attempt)
                        continue

            finally:
                self._pending.pop(request_id, None)
                if not future.done():
                    future.cancel()

            result = {"request_id": request_id, **result}
            self.events.emit("command.completed", device_id=device_id, **result)
            return result

    async def _handle_client(self, reader, writer) -> None:
        task = asyncio.current_task()
        if len(self._clients) >= self.settings.max_connections:
            await close_writer(writer)
            return
        self._clients.add(task)
        session = None
        try:
            message = await asyncio.wait_for(read_message(reader), self.settings.first_message_timeout)
            if message["type"] == "REGISTER":
                device_id = message.get("device_id")
                payload = message["payload"]
                device_type = payload.get("device_type")
                if not device_id or device_type not in ("light", "door_lock"):
                    raise ProtocolError("unsupported TCP device type")
                state = self.validate_state(device_type, payload.get("state"))
                session = DeviceSession(device_id, device_type, writer, state)
                try:
                    self.registry.register(session)
                except ValueError as exc:
                    raise ProtocolError(str(exc)) from exc

                await send_message(writer, make_message("REGISTERED", device_id=device_id))
                self.events.emit("device.connected", device_id=device_id)
                await self._read_device(reader, session)
            elif message["type"] == "LIST_DEVICES":
                await send_message(writer, make_message("DEVICES", {"devices": self.list_devices()}))
            elif message["type"] == "COMMAND":
                device_id = message.get("device_id")
                if not device_id:
                    raise ProtocolError("device_id is required")
                result = await self.submit_command(device_id, message["payload"].get("action"),
                                                   message["payload"].get("value"))
                request_id = result.pop("request_id")
                await send_message(writer, make_message("COMMAND_RESULT", result,
                                                        device_id=device_id, request_id=request_id))
            else:
                raise ProtocolError("first message must be REGISTER, LIST_DEVICES or COMMAND")
        except (ProtocolError, TimeoutError) as exc:
            try:
                await send_message(writer, make_message("ERROR", {"message": str(exc) or "read timeout"}))
            except (OSError, TimeoutError):
                pass
        except (asyncio.IncompleteReadError, OSError):
            pass
        finally:
            if session and self.registry.remove(session):
                self.events.emit("device.disconnected", device_id=session.device_id)
                for owner, future in tuple(self._pending.values()):
                    if owner is session and not future.done():
                        future.set_result({"status": "unknown", "reason": "device_disconnected"})
            await close_writer(writer)
            self._clients.discard(task)

    async def _read_device(self, reader, session: DeviceSession) -> None:
        while True:
            message = await read_message(reader)
            if message.get("device_id") != session.device_id:
                raise ProtocolError("device_id does not match connection")

            if not session.online or self.registry.get(session.device_id) is not session:
                raise ProtocolError("expired connection; register again")
            kind, payload = message["type"], message["payload"]
            if kind == "COMMAND_RESULT":
                pending = self._pending.get(message.get("request_id"))
                if not pending or pending[0] is not session or pending[1].done():
                    continue
                if payload.get("status") not in ("success", "failed"):
                    raise ProtocolError("invalid command result status")
            if kind in {"REPORT", "COMMAND_RESULT"}:
                state = self.validate_state(session.device_type, payload.get("state"))
                session.touch(state)
                self.events.emit("device.state", device_id=session.device_id, state=dict(state))
                if kind == "COMMAND_RESULT":
                    pending[1].set_result({"status": payload["status"], "state": dict(state)})
            elif kind == "HEARTBEAT":
                session.touch()
                self.events.emit("device.heartbeat", device_id=session.device_id)
            else:
                raise ProtocolError("unexpected device message")

    @staticmethod
    def validate_state(device_type, state):
        field = {"light": "power", "door_lock": "locked"}[device_type]
        if not isinstance(state, dict) or type(state.get(field)) is not bool:
            raise ProtocolError(f"state.{field} must be boolean")
        return {field: state[field]}


async def run(settings: Settings, log_db: str | None = "data/events.sqlite") -> None:
    gateway = Gateway(settings)
    gateway.events.subscribe(lambda event: LOG.info("%s", json.dumps(event, ensure_ascii=False)))
    log_store: EventLogStore | None = None
    if log_db:
        log_store = attach_event_log(gateway, EventLogStore(log_db))
        await log_store.start()
    await gateway.start()
    LOG.info("TCP gateway listening on %s", gateway.address)
    try:
        await gateway.serve_forever()
    finally:
        if log_store:
            await log_store.close()
        await gateway.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart dorm TCP bootstrap gateway")
    parser.add_argument("--config", help="TOML configuration file")
    parser.add_argument(
        "--log-db",
        default="data/events.sqlite",
        help="SQLite path for event logs (pass empty string to disable)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = load_settings(args.config)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    try:
        asyncio.run(run(settings, log_db=args.log_db or None))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
