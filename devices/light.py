"""One simulated light. State is in memory; hardware is not required."""

import argparse
import asyncio
import json

from shared.config import load_settings
from shared.protocol import ProtocolError, close_writer, make_message, read_message, send_message


async def run_light(host: str, port: int, device_id: str) -> None:
    reader, writer = await asyncio.open_connection(host, port)
    power = False
    try:
        await send_message(writer, make_message(
            "REGISTER", {"device_type": "light", "state": {"power": power}}, device_id=device_id,
        ))
        reply = await asyncio.wait_for(read_message(reader), 5)
        if reply["type"] != "REGISTERED":
            raise ProtocolError(f"registration rejected: {reply['payload']}")
        print(f"Simulated light {device_id} connected; power=off", flush=True)
        while True:
            message = await read_message(reader)
            if message["type"] != "COMMAND" or message.get("device_id") != device_id:
                raise ProtocolError("unexpected gateway message")
            payload = message["payload"]
            if not message.get("request_id"):
                raise ProtocolError("missing request_id")
            status = "failed"
            if payload.get("action") == "set_power" and type(payload.get("value")) is bool:
                power = payload["value"]
                status = "success"
            result = make_message("COMMAND_RESULT", {"status": status, "state": {"power": power}},
                                  device_id=device_id, request_id=message["request_id"])
            await send_message(writer, result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        await close_writer(writer)


def main() -> None:
    parser = argparse.ArgumentParser(description="Software-only light simulator")
    parser.add_argument("--config")
    parser.add_argument("--host", help="Gateway address; overrides configuration host")
    parser.add_argument("--device-id", default="light_001")
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
        asyncio.run(run_light(args.host or settings.host, settings.tcp_port, args.device_id))
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError) as exc:
        parser.exit(1, f"Light stopped: {exc or 'connection closed'}\n")


if __name__ == "__main__":
    main()
