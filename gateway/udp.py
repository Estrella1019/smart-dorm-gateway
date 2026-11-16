"""UDP sensor reports: raw JSON, with per-device restart and sequence tracking."""
import asyncio
import json
import math
from shared.protocol import validate, ProtocolError


class SensorReceiver(asyncio.DatagramProtocol):
    def __init__(self, gateway):
        self.gateway = gateway
        self.streams = {}

    def datagram_received(self, data, addr):
        try:
            if not 1 <= len(data) <= 8192:
                raise ValueError("invalid datagram size")
            msg = validate(json.loads(data))
            p = msg['payload']
            device_id = msg.get('device_id')
            sid, seq = p.get('session_id'), p.get('seq')
            state = p.get('state')
            if msg['type'] != 'REPORT' or not device_id or p.get('device_type') != 'sensor':
                raise ValueError('expected sensor REPORT')
            if not isinstance(sid, str) or not 1 <= len(sid) <= 128 or type(seq) is not int or seq < 1:
                raise ValueError('invalid session or sequence')
            if not isinstance(state, dict):
                raise ValueError('invalid state')
            for key, low, high in [('temperature', -50, 100), ('humidity', 0, 100)]:
                value = state.get(key)
                if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                    raise ValueError('invalid sensor measurement')
            previous = self.streams.get(device_id)
            retired = set() if previous is None else previous[2].copy()
            if previous:
                old_sid, old_seq, _ = previous
                if sid in retired or (sid == old_sid and seq <= old_seq):
                    return
                if sid != old_sid:
                    if len(retired) >= 128:
                        raise ValueError('restart limit reached; restart gateway')
                    retired.add(old_sid)
            elif len(self.streams) >= self.gateway.settings.max_connections:
                raise ValueError('sensor capacity reached')
            old = self.gateway.registry.get(device_id)
            was_online = old is not None and old.online
            session = self.gateway.registry.upsert_udp_device(device_id, 'sensor',
                {key: state[key] for key in ('temperature', 'humidity')})
            self.streams[device_id] = (sid, seq, retired)
            if not was_online:
                self.gateway.events.emit('device.connected', device_id=device_id)
            self.gateway.events.emit('device.state', device_id=device_id, state=dict(session.state))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            self.gateway.events.emit('udp.rejected', reason='invalid or conflicting sensor report')
