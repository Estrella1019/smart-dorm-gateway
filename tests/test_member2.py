import asyncio
import unittest

from gateway.server import Gateway
from shared.config import Settings
from shared.protocol import close_writer, make_message, read_message, send_message

class Member2ReliableTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # command_timeout 设得很短，方便快速触发重试
        self.gateway = Gateway(Settings(tcp_port=0, command_timeout=0.1, first_message_timeout=0.2))
        await self.gateway.start()
        self.host, self.port = self.gateway.address[:2]
        self.writers = []
        self.tasks = []
        
        # 修改为我们上一轮建议的带软离线的 registry 测试
        from gateway.registry import DeviceRegistry
        self.gateway.registry = DeviceRegistry()

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for writer in self.writers:
            await close_writer(writer)
        await self.gateway.close()

    async def _mock_device(self, device_id="light_001"):
        reader, writer = await asyncio.open_connection(self.host, self.port)
        self.writers.append(writer)
        await send_message(writer, make_message("REGISTER", {
            "device_type": "light", "state": {"power": False}}, device_id=device_id))
        await read_message(reader) # REGISTERED
        return reader, writer

    async def test_command_queuing(self):
        """排队测试：修改现有逻辑后，同设备的多个命令不会再报 busy，而是排队执行。"""
        reader, writer = await self._mock_device()
        
        # 并发提交两条命令
        task1 = asyncio.create_task(self.gateway.submit_command("light_001", "set_power", True))
        task2 = asyncio.create_task(self.gateway.submit_command("light_001", "set_power", False))
        
        # 第一条命令到达
        cmd1 = await read_message(reader)
        self.assertEqual(cmd1["payload"]["value"], True)
        
        # 此时任务2处于排队等待第一条命令执行完成
        await asyncio.sleep(0.05)
        
        # 模拟设备回复第一条
        await send_message(writer, make_message("COMMAND_RESULT", {
            "status": "success", "state": {"power": True}}, device_id="light_001",
            request_id=cmd1["request_id"]))
            
        # 第一条完成后，第二条才发出
        cmd2 = await read_message(reader)
        self.assertEqual(cmd2["payload"]["value"], False)
        
        await send_message(writer, make_message("COMMAND_RESULT", {
            "status": "success", "state": {"power": False}}, device_id="light_001",
            request_id=cmd2["request_id"]))
            
        res1, res2 = await asyncio.gather(task1, task2)
        self.assertEqual(res1["status"], "success")
        self.assertEqual(res2["status"], "success")

    async def test_timeout_and_finite_retry(self):
        """有限重试测试：故意不回复设备，验证发送了 1+2 次并最终返回未知。"""
        reader, writer = await self._mock_device()
        
        task = asyncio.create_task(self.gateway.submit_command("light_001", "set_power", True))
        
        # 收三次相同的 COMMAND (原始发送1次 + 允许的重试2次)
        cmd1 = await read_message(reader)
        cmd2 = await read_message(reader)
        cmd3 = await read_message(reader)
        
        # 验证 request_id 相同 (保证组员 1 的去重有效)
        self.assertEqual(cmd1["request_id"], cmd2["request_id"])
        self.assertEqual(cmd2["request_id"], cmd3["request_id"])
        
        # 最终结果未知
        result = await task
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "confirmation_timeout")

if __name__ == "__main__":
    unittest.main()