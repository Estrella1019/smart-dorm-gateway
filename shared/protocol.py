"""Version 1: four-byte network-order length followed by UTF-8 JSON.

Only framing and common fields live here; each receiver validates its payload.
"""

import asyncio
import json
import struct

VERSION = 1
MAX_MESSAGE_BYTES = 65_536
MESSAGE_TYPES = {
    "REGISTER", "REGISTERED", "REPORT", "HEARTBEAT", "COMMAND",
    "COMMAND_RESULT", "LIST_DEVICES", "DEVICES", "ERROR",
}


class ProtocolError(ValueError):
    pass


def validate(message: object) -> dict:
    if not isinstance(message, dict):
        raise ProtocolError("message must be an object")
    if type(message.get("version")) is not int or message["version"] != VERSION:
        raise ProtocolError("unsupported protocol version")
    kind = message.get("type")
    if not isinstance(kind, str) or kind not in MESSAGE_TYPES:
        raise ProtocolError("unsupported message type")
    if not isinstance(message.get("payload"), dict):
        raise ProtocolError("payload must be an object")
    for field in ("device_id", "request_id"):
        if field in message:
            value = message[field]
            if not isinstance(value, str) or not 1 <= len(value) <= 128:
                raise ProtocolError(f"{field} must be a string of 1..128 characters")
    return message


def make_message(kind: str, payload: dict | None = None, **fields) -> dict:
    return validate({"version": VERSION, "type": kind,
                     "payload": {} if payload is None else payload, **fields})


def encode_message(message: dict) -> bytes:
    validate(message)
    try:
        body = json.dumps(message, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ProtocolError("message cannot be encoded") from exc
    if not 1 <= len(body) <= MAX_MESSAGE_BYTES:
        raise ProtocolError("message too large")
    return struct.pack("!I", len(body)) + body


def _reject_constant(value: str):
    raise ValueError(f"invalid JSON constant: {value}")


async def read_message(reader: asyncio.StreamReader) -> dict:
    # readexactly handles fragmented frames and leaves coalesced frames buffered.
    header = await reader.readexactly(4)
    size = struct.unpack("!I", header)[0]
    if not 1 <= size <= MAX_MESSAGE_BYTES:
        raise ProtocolError("invalid frame length")
    body = await reader.readexactly(size)
    try:
        message = json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError("invalid UTF-8 JSON") from exc
    return validate(message)


async def send_message(writer: asyncio.StreamWriter, message: dict) -> None:
    # A complete frame is written before yielding; don't split headers and bodies.
    writer.write(encode_message(message))
    await asyncio.wait_for(writer.drain(), timeout=5)


async def close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=2)
    except (OSError, TimeoutError):
        pass
