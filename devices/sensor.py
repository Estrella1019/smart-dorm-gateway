"""One simulated temperature/humidity sensor reporting over UDP.

Each datagram carries one protocol message: the same UTF-8 JSON body as a TCP
frame, without the four-byte length prefix. Reports carry a session_id that is
new on every start and a seq that increases by one per report, so a receiver
can spot restarts, loss and reordering.
"""

import argparse
import asyncio
import json
import random
from uuid import uuid4

from shared.config import load_settings
from shared.protocol import encode_message, make_message

DEFAULT_UDP_PORT = 9001
TEMPERATURE_RANGE = (15.0, 35.0)
HUMIDITY_RANGE = (20.0, 90.0)


class Sensor:
    device_type = "sensor"

    def __init__(self, device_id: str, rng: random.Random | None = None):
        self.device_id = device_id
        self.session_id = uuid4().hex
        self.seq = 0
        self._rng = rng or random.Random()
        self.temperature = 24.0
        self.humidity = 50.0

    def sample(self) -> dict:
        # A small random walk looks more like a real room than independent noise.
        self.temperature = min(max(self.temperature + self._rng.uniform(-0.3, 0.3),
                                   TEMPERATURE_RANGE[0]), TEMPERATURE_RANGE[1])
        self.humidity = min(max(self.humidity + self._rng.uniform(-1.0, 1.0),
                                HUMIDITY_RANGE[0]), HUMIDITY_RANGE[1])
        return {"temperature": round(self.temperature, 1), "humidity": round(self.humidity, 1)}

    def next_report(self) -> dict:
        self.seq += 1
        return make_message("REPORT", {
            "device_type": self.device_type, "session_id": self.session_id,
            "seq": self.seq, "state": self.sample(),
        }, device_id=self.device_id)


def encode_datagram(message: dict) -> bytes:
    # Reuse the shared validation and size limit; drop the TCP length prefix.
    return encode_message(message)[4:]


class _FireAndForget(asyncio.DatagramProtocol):
    def error_received(self, exc: Exception) -> None:
        # Nobody listening yet is normal for UDP; keep reporting.
        pass


async def run_sensor(host: str, port: int, device_id: str, interval: float = 5.0,
                     count: int | None = None, sensor: Sensor | None = None) -> None:
    """Send a report every interval seconds; count limits the total (None = forever)."""
    sensor = sensor or Sensor(device_id)
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        _FireAndForget, remote_addr=(host, port))
    print(f"Simulated sensor {device_id} reporting to {host}:{port} every {interval:g}s; "
          f"session={sensor.session_id}", flush=True)
    try:
        sent = 0
        while count is None or sent < count:
            report = sensor.next_report()
            transport.sendto(encode_datagram(report))
            print(json.dumps(report, ensure_ascii=False), flush=True)
            sent += 1
            if count is None or sent < count:
                await asyncio.sleep(interval)
    finally:
        transport.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Software-only UDP temperature/humidity sensor")
    parser.add_argument("--config")
    parser.add_argument("--host", help="Gateway address; overrides configuration host")
    parser.add_argument("--udp-port", type=int, default=DEFAULT_UDP_PORT)
    parser.add_argument("--device-id", default="sensor_001")
    parser.add_argument("--interval", type=float, default=5.0, help="Seconds between reports")
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
        asyncio.run(run_sensor(args.host or settings.host, args.udp_port,
                               args.device_id, args.interval))
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Sensor stopped: {exc}\n")


if __name__ == "__main__":
    main()
