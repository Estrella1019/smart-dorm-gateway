"""Shared TCP device loop. Subclasses own their state and supported actions."""

import argparse
import asyncio
import json

from shared.config import load_settings
from shared.protocol import ProtocolError, close_writer, make_message, read_message, send_message


class TcpDevice:
    device_type = ""

    def __init__(self, device_id: str):
        self.device_id = device_id

    def state(self) -> dict:
        raise NotImplementedError

    def execute(self, action: object, value: object) -> bool:
        """Apply one command to the in-memory state; return False to reject it."""
        raise NotImplementedError

    async def run(self, host: str, port: int) -> None:
        reader, writer = await asyncio.open_connection(host, port)
        try:
            await send_message(writer, make_message(
                "REGISTER", {"device_type": self.device_type, "state": self.state()},
                device_id=self.device_id,
            ))
            reply = await asyncio.wait_for(read_message(reader), 5)
            if reply["type"] != "REGISTERED":
                raise ProtocolError(f"registration rejected: {reply['payload']}")
            print(f"Simulated {self.device_type} {self.device_id} connected; "
                  f"state={json.dumps(self.state())}", flush=True)
            while True:
                message = await read_message(reader)
                if message["type"] != "COMMAND" or message.get("device_id") != self.device_id:
                    raise ProtocolError("unexpected gateway message")
                payload = message["payload"]
                if not message.get("request_id"):
                    raise ProtocolError("missing request_id")
                status = "success" if self.execute(payload.get("action"), payload.get("value")) else "failed"
                result = make_message("COMMAND_RESULT", {"status": status, "state": self.state()},
                                      device_id=self.device_id, request_id=message["request_id"])
                await send_message(writer, result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            await close_writer(writer)


def run_device_cli(device_class: type[TcpDevice], description: str, default_id: str) -> None:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config")
    parser.add_argument("--host", help="Gateway address; overrides configuration host")
    parser.add_argument("--device-id", default=default_id)
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
        device = device_class(args.device_id)
        asyncio.run(device.run(args.host or settings.host, settings.tcp_port))
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError) as exc:
        parser.exit(1, f"{device_class.__name__} stopped: {exc or 'connection closed'}\n")
