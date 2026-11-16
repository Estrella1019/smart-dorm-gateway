"""HTTP adapter sharing the existing Gateway and its event loop."""
import argparse
import asyncio
from pathlib import Path
from aiohttp import web
from gateway.server import Gateway
from shared.config import load_settings
from shared.protocol import ProtocolError


def create_app(gateway):
    app = web.Application(client_max_size=4096)
    sockets = set()
    known = {}

    def snapshot():
        current = {d['device_id']: d for d in gateway.list_devices()}
        for key, device in known.items():
            if key not in current:
                device = {**device, 'online': False}
                current[key] = device
        known.update(current)
        return list(current.values())

    def event_callback(event):
        for queue in tuple(sockets):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait({'event': event, 'devices': snapshot()})

    gateway.events.subscribe(event_callback)

    async def index(request):
        return web.FileResponse(Path(__file__).with_name('index.html'))

    async def devices(request):
        return web.json_response({'devices': snapshot()})

    async def command(request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or body.get('action') not in ('set_power', 'set_locked') or type(body.get('value')) is not bool:
                raise ValueError('action must be set_power/set_locked and value must be boolean')
        except (ValueError, TypeError):
            return web.json_response({'error': 'invalid command'}, status=400)
        try:
            result = await gateway.submit_command(request.match_info['device_id'], body['action'], body['value'])
        except ProtocolError as exc:
            return web.json_response({'error': str(exc)}, status=422)
        return web.json_response(result)

    async def websocket(request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        queue = asyncio.Queue(maxsize=32)
        sockets.add(queue)
        async def sender():
            await ws.send_json({'event': {'event': 'snapshot'}, 'devices': snapshot()})
            while True:
                await ws.send_json(await queue.get())
        task = asyncio.create_task(sender())
        try:
            async for message in ws:
                pass
        finally:
            sockets.discard(queue)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return ws

    app.router.add_get('/', index)
    app.router.add_get('/api/devices', devices)
    app.router.add_post('/api/devices/{device_id}/commands', command)
    app.router.add_get('/ws', websocket)
    return app


async def run(args):
    gateway = Gateway(load_settings(args.config))
    await gateway.start()
    runner = web.AppRunner(create_app(gateway))
    try:
        await runner.setup()
        await web.TCPSite(runner, args.host, args.port).start()
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await gateway.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    try:
        asyncio.run(run(parser.parse_args()))
    except KeyboardInterrupt:
        pass
