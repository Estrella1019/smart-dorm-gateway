import asyncio
import unittest

from client.cli import request
from devices.light import Light, run_light
from gateway.server import Gateway
from shared.config import Settings
from shared.protocol import make_message


class LightTests(unittest.TestCase):
    def test_starts_off(self):
        self.assertEqual(Light("light_001").state(), {"power": False})

    def test_set_power(self):
        light = Light("light_001")
        self.assertTrue(light.execute("set_power", True))
        self.assertEqual(light.state(), {"power": True})
        self.assertTrue(light.execute("set_power", False))
        self.assertEqual(light.state(), {"power": False})

    def test_rejects_unknown_action_or_bad_value(self):
        light = Light("light_001")
        self.assertFalse(light.execute("set_locked", True))
        self.assertFalse(light.execute("set_power", 1))
        self.assertFalse(light.execute("set_power", "on"))
        self.assertEqual(light.state(), {"power": False})


class HeartbeatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gateway = Gateway(Settings(tcp_port=0))
        await self.gateway.start()
        self.host, self.port = self.gateway.address[:2]
        self.heartbeats = []
        self.gateway.events.subscribe(
            lambda event: event["event"] == "device.heartbeat" and self.heartbeats.append(event))
        self.task = None

    async def asyncTearDown(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.gateway.close()

    async def test_sends_periodic_heartbeats(self):
        self.task = asyncio.create_task(run_light(self.host, self.port, "light_001", 0.05))
        async with asyncio.timeout(2):
            while len(self.heartbeats) < 3:
                await asyncio.sleep(0.01)
        self.assertEqual(self.heartbeats[0]["device_id"], "light_001")

    async def test_heartbeats_do_not_break_commands(self):
        self.task = asyncio.create_task(run_light(self.host, self.port, "light_001", 0.01))
        async with asyncio.timeout(2):
            while not self.gateway.list_devices():
                await asyncio.sleep(0.005)
        for value in (True, False, True):
            result = await request(self.host, self.port, make_message(
                "COMMAND", {"action": "set_power", "value": value}, device_id="light_001"))
            self.assertEqual(result["payload"]["status"], "success")
        self.assertTrue(self.heartbeats)

    async def test_zero_interval_disables_heartbeats(self):
        self.task = asyncio.create_task(run_light(self.host, self.port, "light_001", 0))
        async with asyncio.timeout(2):
            while not self.gateway.list_devices():
                await asyncio.sleep(0.005)
        await asyncio.sleep(0.2)
        self.assertEqual(self.heartbeats, [])


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gateway = None
        self.task = None
        # Reserve a free port, then release it so tests can start and stop gateways on it.
        probe = Gateway(Settings(tcp_port=0))
        await probe.start()
        self.port = probe.address[1]
        await probe.close()

    async def asyncTearDown(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.gateway:
            await self.gateway.close()

    async def start_gateway(self):
        self.gateway = Gateway(Settings(tcp_port=self.port))
        await self.gateway.start()

    async def wait_registered(self):
        async with asyncio.timeout(3):
            while not self.gateway.list_devices():
                await asyncio.sleep(0.01)
        return self.gateway.list_devices()[0]

    def start_light(self, light):
        self.task = asyncio.create_task(light.run_forever(
            "127.0.0.1", self.port, heartbeat_interval=0, retry_initial=0.05, retry_max=0.2))

    async def test_reconnects_after_gateway_restart_and_keeps_state(self):
        await self.start_gateway()
        light = Light("light_001")
        self.start_light(light)
        await self.wait_registered()
        result = await request("127.0.0.1", self.port, make_message(
            "COMMAND", {"action": "set_power", "value": True}, device_id="light_001"))
        self.assertEqual(result["payload"]["status"], "success")

        await self.gateway.close()
        await self.start_gateway()
        device = await self.wait_registered()
        self.assertEqual(device["state"], {"power": True})

    async def test_keeps_retrying_until_gateway_starts(self):
        self.start_light(Light("light_001"))
        await asyncio.sleep(0.3)
        self.assertFalse(self.task.done())
        await self.start_gateway()
        device = await self.wait_registered()
        self.assertEqual(device["device_id"], "light_001")
