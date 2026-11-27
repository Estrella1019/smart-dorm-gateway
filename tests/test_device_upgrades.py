import asyncio
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from devices.door_lock import DoorLock
from devices.light import Light
from gateway.server import Gateway
from shared.config import Settings


class ReportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gateway = Gateway(Settings(tcp_port=0, udp_port=0, command_timeout=0.02))
        await self.gateway.start()
        self.addAsyncCleanup(self.gateway.close)
        self.light = Light("lamp")

    async def connect(self, interval):
        task = asyncio.create_task(self.light.run(*self.gateway.address[:2],
                                                  heartbeat_interval=0, report_interval=interval))

        async def stop():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.addAsyncCleanup(stop)
        async with asyncio.timeout(2):
            while not self.light.registered:
                if task.done():
                    await task
                await asyncio.sleep(0.005)

    async def test_report_reconciles_state_after_lost_ack(self):
        self.light.faults.drop_ack = 1.0
        await self.connect(0.3)
        result = await self.gateway.submit_command("lamp", "set_power", True)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(self.light.state(), {"power": True})
        self.assertEqual(self.gateway.list_devices()[0]["state"], {"power": False})
        async with asyncio.timeout(2):
            while self.gateway.list_devices()[0]["state"] != {"power": True}:
                await asyncio.sleep(0.01)

    async def test_zero_disables_reports(self):
        updates = []
        self.gateway.events.subscribe(lambda e: e["event"] == "device.state" and updates.append(e))
        await self.connect(0)
        await asyncio.sleep(0.1)
        self.assertEqual(updates, [])

    async def test_negative_interval_rejected(self):
        for run in (self.light.run, self.light.run_forever):
            with self.assertRaisesRegex(ValueError, "report_interval"):
                await run(*self.gateway.address[:2], report_interval=-1)


class BackoffTests(unittest.IsolatedAsyncioTestCase):
    async def delays(self, seed, jitter):
        light = Light("lamp")
        light.rng = random.Random(seed)
        sleep = AsyncMock(side_effect=[None, None, asyncio.CancelledError()])
        with patch.object(light, "run", side_effect=ConnectionRefusedError), \
                patch("devices.base.asyncio.sleep", sleep):
            with self.assertRaises(asyncio.CancelledError):
                await light.run_forever("localhost", 0, retry_jitter=jitter)
        return [call.args[0] for call in sleep.await_args_list]

    async def test_equal_jitter_and_fixed_delays(self):
        first = await self.delays(1, True)
        second = await self.delays(2, True)
        for delay, a, b in zip([1, 2, 4], first, second, strict=True):
            self.assertTrue(delay / 2 <= a <= delay)
            self.assertTrue(delay / 2 <= b <= delay)
            self.assertNotEqual(a, b)
        self.assertEqual(await self.delays(1, False), [1, 2, 4])


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "device" / "state.json"

    def test_duplicate_refreshes_lru(self):
        light = Light("lamp")
        light.dedup_size = 3
        for n in [0, 1, 2, 0, 3]:
            light.handle_command(f"req_{n}", {})
        self.assertEqual(list(light._recent_results), ["req_2", "req_0", "req_3"])

    def test_restore_state_and_replay_cached_results(self):
        for device_class, field, value in [(Light, "power", True), (DoorLock, "locked", False)]:
            with self.subTest(field=field):
                path = self.path.with_name(field + ".json")
                device = device_class("d")
                device.attach_state_file(path)
                action = "set_" + field
                device.handle_command("old", {})
                first, _ = device.handle_command("a", {"action": action, "value": value})
                device.handle_command("b", {"action": action, "value": not value})
                restored = device_class("d")
                restored.dedup_size = 2
                restored.attach_state_file(path)
                self.assertEqual(list(restored._recent_results), ["a", "b"])
                self.assertEqual(restored.handle_command("a", {"action": action, "value": value}),
                                 (first, True))
                self.assertEqual(restored.state(), {field: not value})
                # Check a non-default state also survives a restart.
                restored.handle_command("c", {"action": action, "value": value})
                restarted = device_class("d")
                restarted.attach_state_file(path)
                self.assertEqual(restarted.state(), {field: value})

    def test_invalid_state_file(self):
        light = Light("d")
        light.attach_state_file(self.path)
        light.handle_command("req", {})
        snapshot = json.loads(self.path.read_text())
        for invalid, reason in [("broken", "Invalid state file"),
                                (json.dumps(snapshot | {"device_type": "door_lock"}), "device_type"),
                                (json.dumps(snapshot | {"state": {"power": 1}}), "boolean")]:
            self.path.write_text(invalid)
            with self.assertRaisesRegex(ValueError, reason):
                light.attach_state_file(self.path)
        with self.assertRaisesRegex(ValueError, "boolean"):
            DoorLock("d").load_state({"locked": "yes"})

    def test_failed_replace_preserves_previous_snapshot_and_allows_retry(self):
        light = Light("d")
        light.attach_state_file(self.path)
        light.handle_command("old", {})
        original = self.path.read_bytes()
        cmd = {"action": "set_power", "value": True}
        with patch("devices.base.os.replace", side_effect=OSError), self.assertRaises(OSError):
            light.handle_command("new", cmd)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assertEqual(light.state(), {"power": False})
        self.assertFalse(light.handle_command("new", cmd)[1])
