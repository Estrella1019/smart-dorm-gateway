import asyncio
import struct
import unittest

from client.cli import request
from devices.light import run_light
from gateway.server import Gateway
from shared.config import Settings
from shared.protocol import MAX_MESSAGE_BYTES, close_writer, make_message, read_message, send_message


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Port 0 is only used internally in tests for an OS-assigned free port.
        self.gateway = Gateway(Settings(tcp_port=0, command_timeout=0.15, first_message_timeout=0.2))
        await self.gateway.start()
        self.host, self.port = self.gateway.address[:2]
        self.writers = []
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for writer in self.writers:
            await close_writer(writer)
        await self.gateway.close()

    async def register(self, device_id="light_001"):
        reader, writer = await asyncio.open_connection(self.host, self.port)
        self.writers.append(writer)
        await send_message(writer, make_message("REGISTER", {
            "device_type": "light", "state": {"power": False}}, device_id=device_id))
        response = await read_message(reader)
        return reader, writer, response

    async def control(self, device_id="light_001", value=True):
        return await request(self.host, self.port, make_message(
            "COMMAND", {"action": "set_power", "value": value}, device_id=device_id))

    async def wait_online(self, expected=True):
        async with asyncio.timeout(2):
            while bool(self.gateway.list_devices()) != expected:
                await asyncio.sleep(0.005)

    async def test_real_light_end_to_end(self):
        self.tasks.append(asyncio.create_task(run_light(self.host, self.port, "light_001")))
        await self.wait_online()
        for value in (True, False):
            result = await self.control(value=value)
            self.assertEqual(result["payload"]["status"], "success")
            listing = await request(self.host, self.port, make_message("LIST_DEVICES"))
            self.assertEqual(listing["payload"]["devices"][0]["state"], {"power": value})

    async def test_missing_device(self):
        result = await self.control("missing")
        self.assertEqual(result["payload"]["reason"], "device_offline")

    async def test_timeout_is_unknown_and_pending_is_cleaned(self):
        await self.register()
        response = await self.control()
        self.assertEqual(response["payload"], {"status": "unknown", "reason": "confirmation_timeout"})
        self.assertEqual(self.gateway._pending, {})

    async def test_disconnect_while_command_pending(self):
        reader, writer, _ = await self.register()
        task = asyncio.create_task(self.control())
        self.tasks.append(task)
        await read_message(reader)
        await close_writer(writer)
        response = await task
        self.assertEqual(response["payload"]["reason"], "device_disconnected")
        await self.wait_online(False)

    async def test_duplicate_registration_does_not_remove_original(self):
        _, writer, original = await self.register()
        self.assertEqual(original["type"], "REGISTERED")
        _, _, duplicate = await self.register()
        self.assertEqual(duplicate["type"], "ERROR")
        self.assertEqual(len(self.gateway.list_devices()), 1)
        await close_writer(writer)
        await self.wait_online(False)

    async def test_busy_device_is_rejected(self):
        reader, writer, _ = await self.register()
        first = asyncio.create_task(self.control())
        self.tasks.append(first)
        command = await read_message(reader)
        second = await self.control(value=False)
        self.assertEqual(second["payload"]["reason"], "device_busy")
        await send_message(writer, make_message("COMMAND_RESULT", {
            "status": "success", "state": {"power": True}}, device_id="light_001",
            request_id=command["request_id"]))
        self.assertEqual((await first)["payload"]["status"], "success")

    async def test_other_device_cannot_acknowledge_command(self):
        reader, _, _ = await self.register("light_001")
        _, attacker, _ = await self.register("light_002")
        command_task = asyncio.create_task(self.control())
        self.tasks.append(command_task)
        command = await read_message(reader)
        await send_message(attacker, make_message("COMMAND_RESULT", {
            "status": "success", "state": {"power": True}}, device_id="light_002",
            request_id=command["request_id"]))
        self.assertEqual((await command_task)["payload"]["status"], "unknown")

    async def test_invalid_client_does_not_crash_server(self):
        reader, writer = await asyncio.open_connection(self.host, self.port)
        self.writers.append(writer)
        writer.write(struct.pack("!I", MAX_MESSAGE_BYTES + 1))
        await writer.drain()
        self.assertEqual((await read_message(reader))["type"], "ERROR")
        listing = await request(self.host, self.port, make_message("LIST_DEVICES"))
        self.assertEqual(listing["type"], "DEVICES")

    async def test_invalid_registration_state(self):
        result = await request(self.host, self.port, make_message("REGISTER", {
            "device_type": "light", "state": []}, device_id="light_001"))
        self.assertEqual(result["type"], "ERROR")

    async def test_idle_first_connection_times_out(self):
        reader, writer = await asyncio.open_connection(self.host, self.port)
        self.writers.append(writer)
        async with asyncio.timeout(2):
            self.assertEqual((await read_message(reader))["type"], "ERROR")


if __name__ == "__main__":
    unittest.main()
