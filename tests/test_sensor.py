import asyncio
import json
import random
import unittest

from devices.sensor import (HUMIDITY_RANGE, TEMPERATURE_RANGE, Sensor, encode_datagram,
                            run_sensor)
from shared.protocol import validate


class SensorTests(unittest.TestCase):
    def test_reports_count_up_within_one_session(self):
        sensor = Sensor("sensor_001", random.Random(1))
        reports = [sensor.next_report() for _ in range(3)]
        self.assertEqual([r["payload"]["seq"] for r in reports], [1, 2, 3])
        self.assertEqual({r["payload"]["session_id"] for r in reports}, {sensor.session_id})
        self.assertEqual(reports[0]["type"], "REPORT")
        self.assertEqual(reports[0]["device_id"], "sensor_001")

    def test_new_start_gets_new_session(self):
        self.assertNotEqual(Sensor("sensor_001").session_id, Sensor("sensor_001").session_id)

    def test_values_stay_in_range(self):
        sensor = Sensor("sensor_001", random.Random(7))
        for _ in range(2000):
            state = sensor.sample()
            self.assertTrue(TEMPERATURE_RANGE[0] <= state["temperature"] <= TEMPERATURE_RANGE[1])
            self.assertTrue(HUMIDITY_RANGE[0] <= state["humidity"] <= HUMIDITY_RANGE[1])

    def test_datagram_is_plain_json_without_length_prefix(self):
        report = Sensor("sensor_001").next_report()
        self.assertEqual(validate(json.loads(encode_datagram(report))), report)


class _Collector(asyncio.DatagramProtocol):
    def __init__(self):
        self.messages = asyncio.Queue()

    def datagram_received(self, data, addr):
        self.messages.put_nowait(json.loads(data))


class SensorOverUdpTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_reports_over_udp(self):
        transport, collector = await asyncio.get_running_loop().create_datagram_endpoint(
            _Collector, local_addr=("127.0.0.1", 0))
        port = transport.get_extra_info("sockname")[1]
        try:
            await run_sensor("127.0.0.1", port, "sensor_001", interval=0.01, count=3)
            received = [await asyncio.wait_for(collector.messages.get(), 2) for _ in range(3)]
        finally:
            transport.close()
        self.assertEqual([m["payload"]["seq"] for m in received], [1, 2, 3])
        self.assertEqual(set(received[0]["payload"]["state"]), {"temperature", "humidity"})

    async def test_keeps_sending_when_nobody_listens(self):
        # Reserve a port and release it so nothing is listening there.
        probe, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0))
        port = probe.get_extra_info("sockname")[1]
        probe.close()
        await asyncio.wait_for(run_sensor("127.0.0.1", port, "sensor_001", interval=0.01, count=5), 2)
