import asyncio
import struct
import unittest

from shared.protocol import MAX_MESSAGE_BYTES, ProtocolError, encode_message, make_message, read_message


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmented_header_and_body(self):
        expected = make_message("REPORT", {"state": {"power": True}}, device_id="灯_1")
        frame = encode_message(expected)
        reader = asyncio.StreamReader()
        task = asyncio.create_task(read_message(reader))
        for byte in frame:
            reader.feed_data(bytes([byte]))
            await asyncio.sleep(0)
        self.assertEqual(await task, expected)

    async def test_coalesced_messages(self):
        a, b = make_message("LIST_DEVICES"), make_message("HEARTBEAT", device_id="light_001")
        reader = asyncio.StreamReader()
        reader.feed_data(encode_message(a) + encode_message(b))
        self.assertEqual(await read_message(reader), a)
        self.assertEqual(await read_message(reader), b)

    async def test_oversized_frame_rejected_before_body(self):
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack("!I", MAX_MESSAGE_BYTES + 1))
        with self.assertRaises(ProtocolError):
            await read_message(reader)

    async def test_partial_frame_eof(self):
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack("!I", 10) + b"{}")
        reader.feed_eof()
        with self.assertRaises(asyncio.IncompleteReadError):
            await read_message(reader)

    async def test_invalid_wire_json(self):
        bodies = [b"\xff", b"[]", b'{"version":1,"type":"REPORT","payload":{"x":NaN}}',
                  b'{"version":true,"type":"REPORT","payload":{}}']
        for body in bodies:
            with self.subTest(body=body):
                reader = asyncio.StreamReader()
                reader.feed_data(struct.pack("!I", len(body)) + body)
                with self.assertRaises(ProtocolError):
                    await read_message(reader)
