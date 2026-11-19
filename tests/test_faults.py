import asyncio
import json
import random
import unittest

from devices.base import Faults
from devices.light import Light
from devices.sensor import Sensor, run_sensor
from shared.protocol import close_writer, make_message, read_message, send_message


class FaultsTests(unittest.TestCase):
    def test_off_by_default(self):
        self.assertEqual(Light("light_001").faults, Faults(0, 0, 0))

    def test_rejects_bad_values(self):
        for kwargs in ({"delay": -1}, {"drop_ack": 1.5}, {"drop_ack": -0.1}, {"disconnect_after": -1}):
            with self.assertRaises(ValueError):
                Faults(**kwargs)


class FaultyLightTests(unittest.IsolatedAsyncioTestCase):
    """A stand-in gateway that accepts every connection, so reconnects can be observed."""

    async def asyncSetUp(self):
        self.connections = asyncio.Queue()
        self.writers = []
        self.server = await asyncio.start_server(
            lambda r, w: self.connections.put_nowait((r, w)), "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.light = Light("light_001")
        self.task = None

    async def asyncTearDown(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        for writer in self.writers:
            await close_writer(writer)
        self.server.close()
        await self.server.wait_closed()

    def start(self):
        self.task = asyncio.create_task(self.light.run_forever(
            "127.0.0.1", self.port, heartbeat_interval=0, retry_initial=0.05))

    async def accept(self):
        reader, writer = await asyncio.wait_for(self.connections.get(), 2)
        self.writers.append(writer)
        self.assertEqual((await read_message(reader))["type"], "REGISTER")
        await send_message(writer, make_message("REGISTERED", device_id="light_001"))
        return reader, writer

    async def send_command(self, writer, request_id, value):
        await send_message(writer, make_message(
            "COMMAND", {"action": "set_power", "value": value},
            device_id="light_001", request_id=request_id))

    async def test_delay_slows_down_replies(self):
        self.light.faults = Faults(delay=0.2)
        self.start()
        reader, writer = await self.accept()
        loop = asyncio.get_running_loop()
        started = loop.time()
        await self.send_command(writer, "req_a", True)
        result = await asyncio.wait_for(read_message(reader), 2)
        self.assertGreaterEqual(loop.time() - started, 0.2)
        self.assertEqual(result["payload"]["status"], "success")

    async def test_dropped_ack_still_executes_and_resend_gets_answer(self):
        self.light.faults = Faults(drop_ack=1.0)
        self.start()
        reader, writer = await self.accept()
        await self.send_command(writer, "req_a", True)
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(read_message(reader), 0.2)
        self.assertEqual(self.light.state(), {"power": True})

        # The gateway resends with the same request_id once the link recovers:
        # the light answers from memory and does not run the command again.
        self.light.faults = Faults()
        self.light.execute("set_power", False)
        await self.send_command(writer, "req_a", True)
        result = await asyncio.wait_for(read_message(reader), 2)
        self.assertEqual(result["payload"], {"status": "success", "state": {"power": True}})
        self.assertEqual(self.light.state(), {"power": False})

    async def test_disconnect_after_drops_link_then_reconnects(self):
        self.light.faults = Faults(disconnect_after=0.15)
        self.start()
        reader, _ = await self.accept()
        with self.assertRaises(asyncio.IncompleteReadError):
            await asyncio.wait_for(read_message(reader), 2)
        await self.accept()


class _Collector(asyncio.DatagramProtocol):
    def __init__(self):
        self.messages = []

    def datagram_received(self, data, addr):
        self.messages.append(json.loads(data))


class FaultySensorTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, drop_rate, count=40):
        transport, collector = await asyncio.get_running_loop().create_datagram_endpoint(
            _Collector, local_addr=("127.0.0.1", 0))
        port = transport.get_extra_info("sockname")[1]
        try:
            await run_sensor("127.0.0.1", port, "sensor_001", interval=0.001, count=count,
                             sensor=Sensor("sensor_001", random.Random(3)), drop_rate=drop_rate)
            await asyncio.sleep(0.1)
        finally:
            transport.close()
        return [m["payload"]["seq"] for m in collector.messages]

    async def test_drop_rate_leaves_gaps_in_seq(self):
        seqs = await self.collect(0.5)
        self.assertTrue(0 < len(seqs) < 40)
        self.assertEqual(seqs, sorted(seqs))
        self.assertLessEqual(max(seqs), 40)

    async def test_drop_everything(self):
        self.assertEqual(await self.collect(1.0, count=5), [])

    async def test_rejects_bad_drop_rate(self):
        with self.assertRaises(ValueError):
            await run_sensor("127.0.0.1", 9, "sensor_001", count=1, drop_rate=2)
