import asyncio
import unittest
from gateway.server import Gateway
from shared.config import Settings
from devices.door_lock import DoorLock
from devices.sensor import Sensor, encode_datagram
from shared.protocol import make_message

class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gateway = Gateway(Settings(tcp_port=0, udp_port=0))
        await self.gateway.start()
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.gateway.close()

    async def test_real_door_control_and_reconnect(self):
        lock = DoorLock('lock')
        self.tasks.append(asyncio.create_task(lock.run_forever(*self.gateway.address[:2],
            heartbeat_interval=.02, retry_initial=.02, retry_max=.05)))
        async with asyncio.timeout(2):
            while not self.gateway.list_devices():
                await asyncio.sleep(.01)
        result = await self.gateway.submit_command('lock', 'set_locked', False)
        self.assertEqual(result['status'], 'success')
        self.assertFalse(lock.locked)
        session = self.gateway.registry.get('lock')
        self.gateway.registry.expire_inactive(0, session.last_seen_mono+1)
        async with asyncio.timeout(2):
            while self.gateway.registry.get('lock') is session:
                await asyncio.sleep(.01)
        self.assertEqual((await self.gateway.submit_command('lock', 'set_locked', True))['status'], 'success')

    async def test_udp_reordering_restart_and_collision(self):
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol, remote_addr=self.gateway._udp.get_extra_info('sockname'))
        sensor = Sensor('sensor')
        async def send(message):
            transport.sendto(encode_datagram(message))
            await asyncio.sleep(.03)
        try:
            first = sensor.next_report()
            await send(first)
            newer = sensor.next_report()
            newer['payload']['state']['temperature'] = 30
            await send(newer)
            await send(first)
            self.assertEqual(self.gateway.registry.get('sensor').state['temperature'], 30)
            restarted = Sensor('sensor').next_report()
            restarted['payload']['state']['temperature'] = 20
            await send(restarted)
            await send(newer)
            self.assertEqual(self.gateway.registry.get('sensor').state['temperature'], 20)
            transport.sendto(b'not json')
            await asyncio.sleep(.03)
            self.assertTrue(self.gateway.registry.get('sensor').online)
        finally:
            transport.close()

    async def test_late_result_does_not_overwrite_state(self):
        from shared.protocol import send_message, read_message, close_writer
        reader, writer = await asyncio.open_connection(*self.gateway.address[:2])
        try:
            await send_message(writer, make_message('REGISTER',
                {'device_type': 'light', 'state': {'power': False}}, device_id='lamp'))
            await read_message(reader)
            async def control(value):
                task = asyncio.create_task(self.gateway.submit_command('lamp', 'set_power', value))
                cmd = await read_message(reader)
                reply = make_message('COMMAND_RESULT', {'status': 'success', 'state': {'power': value}},
                    device_id='lamp', request_id=cmd['request_id'])
                await send_message(writer, reply)
                self.assertEqual((await task)['status'], 'success')
                return reply
            old = await control(True)
            await control(False)
            await send_message(writer, old)
            await asyncio.sleep(.03)
            self.assertFalse(self.gateway.registry.get('lamp').state['power'])
            from gateway.udp import SensorReceiver
            receiver = SensorReceiver(self.gateway)
            receiver.datagram_received(encode_datagram(Sensor('lamp').next_report()), ('127.0.0.1', 1))
            self.assertEqual(self.gateway.registry.get('lamp').protocol, 'TCP')
        finally:
            await close_writer(writer)

    async def test_web_door_and_udp_snapshot(self):
        from aiohttp.test_utils import TestClient, TestServer
        from web.server import create_app
        from devices.sensor import run_sensor
        client = TestClient(TestServer(create_app(self.gateway)))
        await client.start_server()
        self.tasks.append(asyncio.create_task(DoorLock('web_lock').run(*self.gateway.address[:2])))
        try:
            async with asyncio.timeout(2):
                while self.gateway.registry.get('web_lock') is None:
                    await asyncio.sleep(.01)
            ws = await client.ws_connect('/ws')
            await ws.receive_json(timeout=2)
            response = await client.post('/api/devices/web_lock/commands',
                json={'action': 'set_locked', 'value': False})
            self.assertEqual((await response.json())['state'], {'locked': False})
            await run_sensor(*self.gateway._udp.get_extra_info('sockname')[:2], 'web_sensor', count=1)
            async with asyncio.timeout(2):
                while True:
                    msg = await ws.receive_json()
                    if any(d['device_id'] == 'web_sensor' for d in msg['devices']):
                        break
            response = await client.get('/api/devices')
            devices = (await response.json())['devices']
            self.assertEqual(len(devices), 2)
            self.assertIn('temperature', next(d for d in devices if d['device_id']=='web_sensor')['state'])
            await ws.close()
        finally:
            await client.close()
