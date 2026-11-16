import asyncio
import unittest

from devices.door_lock import DoorLock, run_door_lock
from shared.protocol import close_writer, make_message, read_message, send_message


class DoorLockTests(unittest.TestCase):
    def test_starts_locked(self):
        self.assertEqual(DoorLock("lock_001").state(), {"locked": True})

    def test_set_locked(self):
        lock = DoorLock("lock_001")
        self.assertTrue(lock.execute("set_locked", False))
        self.assertEqual(lock.state(), {"locked": False})
        self.assertTrue(lock.execute("set_locked", True))
        self.assertEqual(lock.state(), {"locked": True})

    def test_rejects_light_action_and_bad_value(self):
        lock = DoorLock("lock_001")
        self.assertFalse(lock.execute("set_power", False))
        self.assertFalse(lock.execute("set_locked", 0))
        self.assertEqual(lock.state(), {"locked": True})


class DoorLockOverTcpTests(unittest.IsolatedAsyncioTestCase):
    """Talks to a stand-in gateway, since the real one only registers lights for now."""

    async def asyncSetUp(self):
        self.connected = asyncio.get_running_loop().create_future()
        self.server = await asyncio.start_server(
            lambda r, w: self.connected.done() or self.connected.set_result((r, w)),
            "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.task = asyncio.create_task(run_door_lock("127.0.0.1", port, "lock_001", 0))
        self.reader, self.writer = await asyncio.wait_for(self.connected, 2)

    async def asyncTearDown(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        await close_writer(self.writer)
        self.server.close()
        await self.server.wait_closed()

    async def command(self, request_id, value):
        await send_message(self.writer, make_message(
            "COMMAND", {"action": "set_locked", "value": value},
            device_id="lock_001", request_id=request_id))
        return await asyncio.wait_for(read_message(self.reader), 2)

    async def test_registers_and_unlocks(self):
        register = await read_message(self.reader)
        self.assertEqual(register["type"], "REGISTER")
        self.assertEqual(register["payload"], {"device_type": "door_lock", "state": {"locked": True}})
        await send_message(self.writer, make_message("REGISTERED", device_id="lock_001"))

        result = await self.command("req_a", False)
        self.assertEqual(result["request_id"], "req_a")
        self.assertEqual(result["payload"], {"status": "success", "state": {"locked": False}})
        # A resent unlock after the door was locked again must not reopen it.
        await self.command("req_b", True)
        again = await self.command("req_a", False)
        self.assertEqual(again["payload"]["state"], {"locked": False})
        self.assertEqual((await self.command("req_c", True))["payload"]["state"], {"locked": True})
