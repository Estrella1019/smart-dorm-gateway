"""Shared TCP device loop. Subclasses own their state and supported actions."""

import argparse
import asyncio
import json
from collections import OrderedDict

from shared.config import load_settings
from shared.protocol import ProtocolError, close_writer, make_message, read_message, send_message


RETRYABLE_ERRORS = (OSError, TimeoutError, asyncio.IncompleteReadError, ProtocolError)


def _raise_if_cancelling() -> None:
    # Python 3.11's wait_for can swallow a cancel that races with completion;
    # honour the pending request so a stopped device doesn't keep reconnecting.
    task = asyncio.current_task()
    if task and task.cancelling():
        raise asyncio.CancelledError


class TcpDevice:
    device_type = ""
    dedup_size = 100

    def __init__(self, device_id: str):
        self.device_id = device_id
        # The heartbeat task and command loop share one writer; keep frames whole.
        self._send_lock = asyncio.Lock()
        self.registered = False
        # request_id -> result payload, oldest first. Kept across reconnects so a
        # command resent after a lost acknowledgment is answered, not re-executed.
        self._recent_results: OrderedDict[str, dict] = OrderedDict()

    def state(self) -> dict:
        raise NotImplementedError

    def execute(self, action: object, value: object) -> bool:
        """Apply one command to the in-memory state; return False to reject it."""
        raise NotImplementedError

    def handle_command(self, request_id: str, payload: dict) -> tuple[dict, bool]:
        """Return (result payload, is_duplicate); a repeated request_id is not re-executed."""
        cached = self._recent_results.get(request_id)
        if cached is not None:
            return dict(cached), True
        status = "success" if self.execute(payload.get("action"), payload.get("value")) else "failed"
        result = {"status": status, "state": self.state()}
        self._recent_results[request_id] = result
        if len(self._recent_results) > self.dedup_size:
            self._recent_results.popitem(last=False)
        return dict(result), False

    async def _send(self, writer: asyncio.StreamWriter, message: dict) -> None:
        async with self._send_lock:
            await send_message(writer, message)

    async def _heartbeat(self, writer: asyncio.StreamWriter, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            await self._send(writer, make_message("HEARTBEAT", device_id=self.device_id))

    async def run(self, host: str, port: int, heartbeat_interval: float = 5.0) -> None:
        """heartbeat_interval <= 0 disables heartbeats."""
        reader, writer = await asyncio.open_connection(host, port)
        heartbeat = None
        try:
            await send_message(writer, make_message(
                "REGISTER", {"device_type": self.device_type, "state": self.state()},
                device_id=self.device_id,
            ))
            reply = await asyncio.wait_for(read_message(reader), 5)
            if reply["type"] != "REGISTERED":
                raise ProtocolError(f"registration rejected: {reply['payload']}")
            self.registered = True
            _raise_if_cancelling()
            print(f"Simulated {self.device_type} {self.device_id} connected; "
                  f"state={json.dumps(self.state())}", flush=True)
            if heartbeat_interval > 0:
                heartbeat = asyncio.create_task(self._heartbeat(writer, heartbeat_interval))
            while True:
                message = await read_message(reader)
                if message["type"] != "COMMAND" or message.get("device_id") != self.device_id:
                    raise ProtocolError("unexpected gateway message")
                payload = message["payload"]
                if not message.get("request_id"):
                    raise ProtocolError("missing request_id")
                result_payload, duplicate = self.handle_command(message["request_id"], payload)
                result = make_message("COMMAND_RESULT", result_payload,
                                      device_id=self.device_id, request_id=message["request_id"])
                await self._send(writer, result)
                if duplicate:
                    print(f"duplicate request_id {message['request_id']}; "
                          "replaying earlier result without executing", flush=True)
                print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            if heartbeat:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            await close_writer(writer)

    async def run_forever(self, host: str, port: int, heartbeat_interval: float = 5.0,
                          retry_initial: float = 1.0, retry_max: float = 30.0) -> None:
        """Reconnect with exponential backoff; in-memory state survives reconnects."""
        delay = retry_initial
        while True:
            self.registered = False
            reason = "connection closed"
            try:
                await self.run(host, port, heartbeat_interval)
            except RETRYABLE_ERRORS as exc:
                reason = str(exc) or type(exc).__name__
            _raise_if_cancelling()
            if self.registered:
                # The last attempt got through, so start the backoff over.
                delay = retry_initial
            print(f"{self.device_type} {self.device_id} disconnected ({reason}); "
                  f"retrying in {delay:g}s", flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, retry_max)


def run_device_cli(device_class: type[TcpDevice], description: str, default_id: str) -> None:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config")
    parser.add_argument("--host", help="Gateway address; overrides configuration host")
    parser.add_argument("--device-id", default=default_id)
    parser.add_argument("--heartbeat-interval", type=float, default=5.0,
                        help="Seconds between heartbeats; 0 disables them")
    parser.add_argument("--retry-max", type=float, default=30.0,
                        help="Longest wait between reconnect attempts, in seconds")
    parser.add_argument("--no-reconnect", action="store_true",
                        help="Exit on the first disconnect instead of reconnecting")
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
        device = device_class(args.device_id)
        host = args.host or settings.host
        if args.no_reconnect:
            asyncio.run(device.run(host, settings.tcp_port, args.heartbeat_interval))
        else:
            asyncio.run(device.run_forever(host, settings.tcp_port, args.heartbeat_interval,
                                           retry_max=args.retry_max))
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError) as exc:
        parser.exit(1, f"{device_class.__name__} stopped: {exc or 'connection closed'}\n")
