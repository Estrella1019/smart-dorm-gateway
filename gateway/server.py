"""Runnable TCP bootstrap. No retries, heartbeat expiry, HTTP or persistence yet."""

import argparse
import asyncio
import json
import logging
from uuid import uuid4

from shared.config import Settings, load_settings
from shared.protocol import ProtocolError, close_writer, make_message, read_message, send_message
from gateway.events import EventBus
from gateway.registry import DeviceRegistry, DeviceSession, utc_now

LOG = logging.getLogger(__name__)


class Gateway:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.events = EventBus()
        self.registry = DeviceRegistry()
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.Task] = set()
        # Correlate against the session, not a client-supplied device ID alone.
        self._pending: dict[str, tuple[DeviceSession, asyncio.Future]] = {}

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, self.settings.host, self.settings.tcp_port,
            limit=131072,
        )

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
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        tasks = list(self._clients)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def list_devices(self) -> list[dict]:
        return self.registry.list_devices()

    async def send_to_device(self, device_id: str, message: dict) -> None:
        session = self.registry.get(device_id)
        if session is None:
            raise ConnectionError("device offline")
        await send_message(session.writer, message)

    async def submit_command(self, device_id: str, action: str, value: bool) -> dict:
        if action != "set_power" or type(value) is not bool:
            raise ProtocolError("bootstrap supports set_power with a boolean value only")
        request_id = uuid4().hex
        session = self.registry.get(device_id)
        if session is None:
            return {"request_id": request_id, "status": "failed", "reason": "device_offline"}
        if session.command_lock.locked():
            return {"request_id": request_id, "status": "failed", "reason": "device_busy"}
        # This version rejects overlapping commands, rather than implementing a queue.
        async with session.command_lock:
            future = asyncio.get_running_loop().create_future()
            self._pending[request_id] = (session, future)
            self.events.emit("command.submitted", device_id=device_id, request_id=request_id)
            try:
                await send_message(session.writer, make_message(
                    "COMMAND", {"action": action, "value": value},
                    device_id=device_id, request_id=request_id,
                ))
                result = await asyncio.wait_for(future, self.settings.command_timeout)
            except TimeoutError:
                result = {"status": "unknown", "reason": "confirmation_timeout"}
            except (OSError, ConnectionError):
                result = {"status": "unknown", "reason": "device_disconnected"}
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
                if not device_id or payload.get("device_type") != "light":
                    raise ProtocolError("registration requires device_id and device_type=light")
                state = payload.get("state", {})
                if not isinstance(state, dict) or type(state.get("power")) is not bool:
                    raise ProtocolError("registration requires state.power boolean")
                session = DeviceSession(device_id, "light", writer, {"power": state["power"]})
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
            session.last_seen = utc_now()
            kind = message["type"]
            payload = message["payload"]
            if kind in {"REPORT", "COMMAND_RESULT"}:
                state = payload.get("state")
                if not isinstance(state, dict) or type(state.get("power")) is not bool:
                    raise ProtocolError("state.power must be boolean")
                if kind == "COMMAND_RESULT":
                    if payload.get("status") not in ("success", "failed"):
                        raise ProtocolError("invalid command result status")
                    pending = self._pending.get(message.get("request_id"))
                    if not pending or pending[0] is not session:
                        # Ignore stale/unsolicited acknowledgments; REPORT can reconcile state.
                        continue
                    if not pending[1].done():
                        pending[1].set_result({"status": payload["status"], "state": dict(state)})
                session.state = {"power": state["power"]}
                self.events.emit("device.state", device_id=session.device_id, state=dict(session.state))
            elif kind == "HEARTBEAT":
                self.events.emit("device.heartbeat", device_id=session.device_id)
            else:
                raise ProtocolError("unexpected device message")


async def run(settings: Settings) -> None:
    gateway = Gateway(settings)
    gateway.events.subscribe(lambda event: LOG.info("%s", json.dumps(event, ensure_ascii=False)))
    await gateway.start()
    LOG.info("TCP gateway listening on %s", gateway.address)
    try:
        await gateway.serve_forever()
    finally:
        await gateway.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart dorm TCP bootstrap gateway")
    parser.add_argument("--config", help="TOML configuration file")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = load_settings(args.config)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
