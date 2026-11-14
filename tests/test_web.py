import asyncio
import unittest
from aiohttp.test_utils import TestClient, TestServer
from gateway.server import Gateway
from shared.config import Settings
from devices.light import run_light
from web.server import create_app


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_socket_light_and_websocket(self):
        gateway = Gateway(Settings(tcp_port=0))
        await gateway.start()
        client = TestClient(TestServer(create_app(gateway)))
        await client.start_server()
        light = asyncio.create_task(run_light(*gateway.address[:2], 'web_light'))
        try:
            async with asyncio.timeout(3):
                while not gateway.list_devices():
                    await asyncio.sleep(.01)
            ws = await client.ws_connect('/ws')
            message = await ws.receive_json(timeout=2)
            self.assertEqual(message['devices'][0]['device_id'], 'web_light')
            response = await client.post('/api/devices/web_light/commands', json={'action': 'set_power', 'value': True})
            self.assertEqual((await response.json())['status'], 'success')
            response = await client.get('/api/devices')
            self.assertTrue((await response.json())['devices'][0]['state']['power'])
            response = await client.post('/api/devices/web_light/commands', json={'action': 'set_power', 'value': 'true'})
            self.assertEqual(response.status, 400)
            response = await client.post('/api/devices/web_light/commands', json={'action': 'set_locked', 'value': True})
            self.assertEqual(response.status, 422)
            async with asyncio.timeout(3):
                while True:
                    event = await ws.receive_json()
                    if event['event']['event'] == 'command.completed':
                        self.assertEqual(event['event']['status'], 'success')
                        break
            light.cancel()
            await asyncio.gather(light, return_exceptions=True)
            async with asyncio.timeout(3):
                while True:
                    event = await ws.receive_json()
                    if event['event']['event'] == 'device.disconnected':
                        self.assertFalse(event['devices'][0]['online'])
                        break
            await ws.close()
            ws = await client.ws_connect('/ws')
            self.assertFalse((await ws.receive_json(timeout=2))['devices'][0]['online'])
            await ws.close()
        finally:
            light.cancel()
            await asyncio.gather(light, return_exceptions=True)
            await client.close()
            await gateway.close()
