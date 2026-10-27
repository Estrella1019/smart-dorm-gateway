import argparse
import asyncio
import json

from shared.config import load_settings
from shared.protocol import close_writer, make_message, read_message, send_message


async def request(host: str, port: int, message: dict, timeout: float = 10) -> dict:
    async def exchange():
        reader, writer = await asyncio.open_connection(host, port)
        try:
            await send_message(writer, message)
            return await read_message(reader)
        finally:
            await close_writer(writer)
    return await asyncio.wait_for(exchange(), timeout)


def main() -> None:
    parser = argparse.ArgumentParser(description="List or control simulated devices")
    parser.add_argument("--config")
    parser.add_argument("--host")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List currently connected devices")
    power = sub.add_parser("power", help="Set the simulated light state")
    power.add_argument("device_id")
    power.add_argument("value", choices=["on", "off"])
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
        message = make_message("LIST_DEVICES") if args.command == "list" else make_message(
            "COMMAND", {"action": "set_power", "value": args.value == "on"}, device_id=args.device_id,
        )
        response = asyncio.run(request(args.host or settings.host, settings.tcp_port, message,
                                       timeout=settings.command_timeout + 10))
    except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError) as exc:
        parser.exit(1, f"Request failed: {exc or 'connection closed'}\n")
    print(json.dumps(response, ensure_ascii=False, indent=2))
    if response["type"] == "ERROR":
        raise SystemExit(1)
    if response["type"] == "COMMAND_RESULT" and response["payload"]["status"] != "success":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
