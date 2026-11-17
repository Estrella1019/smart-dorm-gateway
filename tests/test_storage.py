import asyncio
import tempfile
import unittest
from pathlib import Path

from devices.light import Light
from gateway.server import Gateway
from gateway.storage import EventLogStore, attach_event_log
from shared.config import Settings
from shared.protocol import make_message, send_message, read_message, close_writer


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "events.sqlite"
        self.gateway = Gateway(Settings(tcp_port=0, command_timeout=0.2))
        self.store = attach_event_log(self.gateway, EventLogStore(self.db_path))
        await self.store.start()
        await self.gateway.start()
        self.host, self.port = self.gateway.address[:2]

    async def asyncTearDown(self):
        await self.store.close()
        await self.gateway.close()
        self.tmp.cleanup()

    async def test_command_flow_logged_by_request_id(self):
        task = asyncio.create_task(Light("lamp_log").run(self.host, self.port, heartbeat_interval=0.05))
        try:
            async with asyncio.timeout(2):
                while not self.gateway.list_devices():
                    await asyncio.sleep(0.01)
            result = await self.gateway.submit_command("lamp_log", "set_power", True)
            await asyncio.sleep(0.1)
            trail = self.store.query_command_trail(result["request_id"])
            names = [row["event"] for row in trail]
            self.assertIn("command.submitted", names)
            self.assertIn("command.sent", names)
            self.assertIn("command.completed", names)
            self.assertEqual(trail[-1]["status"], "success")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_offline_and_udp_events_persisted(self):
        task = asyncio.create_task(Light("gone").run(self.host, self.port, heartbeat_interval=0.02))
        async with asyncio.timeout(2):
            while not self.gateway.list_devices():
                await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0.15)
        disconnected = self.store.query_recent(event_name="device.disconnected", limit=10)
        self.assertTrue(disconnected)

        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=self.gateway._udp.get_extra_info("sockname"),
        )
        from devices.sensor import Sensor, encode_datagram

        try:
            transport.sendto(encode_datagram(Sensor("s1").next_report()))
            await asyncio.sleep(0.1)
            states = self.store.query_recent(event_name="device.state", device_id="s1", limit=5)
            self.assertTrue(states)
        finally:
            transport.close()

    async def test_timeout_retry_logged(self):
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            await send_message(
                writer,
                make_message("REGISTER", {"device_type": "light", "state": {"power": False}}, device_id="mute"),
            )
            await read_message(reader)
            result = await self.gateway.submit_command("mute", "set_power", True)
            await asyncio.sleep(0.05)
            self.assertEqual(result["status"], "unknown")
            retries = self.store.query_recent(event_name="command.timeout_retry", limit=10)
            self.assertGreaterEqual(len(retries), 1)
        finally:
            await close_writer(writer)


if __name__ == "__main__":
    unittest.main()