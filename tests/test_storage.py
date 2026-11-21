import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from devices.light import Light
from gateway.server import Gateway
from gateway.storage import EventLogStore, attach_event_log, parse_query_limit
from unittest.mock import patch
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
        await self.gateway.close()
        await self.store.drain_and_close()
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

    def test_parse_query_limit_rejects_invalid(self):
        self.assertEqual(parse_query_limit(None), 50)
        self.assertIsNone(parse_query_limit("abc"))
        self.assertIsNone(parse_query_limit("-1"))
        self.assertIsNone(parse_query_limit("0"))
        self.assertIsNone(parse_query_limit("201"))
        self.assertEqual(parse_query_limit("10"), 10)
        self.assertEqual(parse_query_limit("200"), 200)

    async def test_shutdown_drains_disconnect_event(self):
        task = asyncio.create_task(Light("bye").run(self.host, self.port, heartbeat_interval=0.05))
        async with asyncio.timeout(2):
            while not self.gateway.list_devices():
                await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await self.gateway.close()
        await self.store.drain_and_close()
        rows = self.store.query_recent(event_name="device.disconnected", device_id="bye", limit=10)
        self.assertTrue(rows)

    async def test_writer_survives_insert_failure(self):
        self.store.enqueue({
            "event": "device.connected",
            "timestamp": "2020-01-01T00:00:00+00:00",
            "device_id": "x",
        })
        with patch.object(EventLogStore, "_insert", side_effect=sqlite3.OperationalError("disk fail")):
            await asyncio.sleep(0.1)
        self.store.enqueue({
            "event": "device.connected",
            "timestamp": "2020-01-01T00:00:01+00:00",
            "device_id": "y",
        })
        await asyncio.sleep(0.15)
        self.assertGreaterEqual(self.store.count_events(), 1)

    async def test_writer_uses_one_thread_and_start_is_ready(self):
        import threading
        self.assertEqual(self.store.count_events(), 0)
        writer_thread = await asyncio.get_running_loop().run_in_executor(
            self.store._executor, threading.get_ident)
        threads = []
        original = self.store._insert
        def insert(event):
            threads.append(threading.get_ident())
            return original(event)
        with patch.object(self.store, "_insert", side_effect=insert):
            for i in range(20):
                self.store.enqueue({"event": "device.state", "device_id": str(i)})
            await self.store.close()
        self.assertEqual(self.store.count_events(), 20)
        self.assertEqual(set(threads), {writer_thread})


if __name__ == "__main__":
    unittest.main()