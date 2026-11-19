#!/usr/bin/env python3
"""Repeatable system scenarios for member 4 acceptance (stdout JSON summary)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from devices.door_lock import DoorLock
from devices.light import Light
from devices.sensor import Sensor, encode_datagram
from gateway.server import Gateway
from gateway.storage import EventLogStore, attach_event_log
from shared.config import Settings
from shared.protocol import make_message, send_message, read_message, close_writer


async def _wait_devices(gateway: Gateway, minimum: int, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(gateway.list_devices()) >= minimum:
            return
        await asyncio.sleep(0.02)
    raise TimeoutError(f"expected at least {minimum} devices online")


async def scenario_multi_device(db_path: Path) -> dict:
    gateway = Gateway(Settings(tcp_port=0, udp_port=0, command_timeout=0.2))
    store = EventLogStore(db_path)
    attach_event_log(gateway, store)
    await store.start()
    await gateway.start()
    host, port = gateway.address[:2]
    tasks = [
        asyncio.create_task(Light("light_a").run(host, port, heartbeat_interval=0.05)),
        asyncio.create_task(DoorLock("lock_b").run(host, port, heartbeat_interval=0.05)),
    ]
    try:
        await _wait_devices(gateway, 2)
        light_result = await gateway.submit_command("light_a", "set_power", True)
        lock_result = await gateway.submit_command("lock_b", "set_locked", False)
        await asyncio.sleep(0.1)
        trail = store.query_command_trail(light_result["request_id"])
        return {
            "name": "multi_device",
            "light_status": light_result["status"],
            "lock_status": lock_result["status"],
            "logged_command_events": len(trail),
            "event_count": store.count_events(),
        }
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await gateway.close()
        await store.drain_and_close()


async def scenario_offline_command(db_path: Path) -> dict:
    gateway = Gateway(Settings(tcp_port=0, command_timeout=0.15, heartbeat_timeout=0.05))
    store = EventLogStore(db_path)
    attach_event_log(gateway, store)
    await store.start()
    await gateway.start()
    host, port = gateway.address[:2]
    task = asyncio.create_task(Light("orphan").run(host, port, heartbeat_interval=0.02))
    try:
        await _wait_devices(gateway, 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0.15)
        result = await gateway.submit_command("orphan", "set_power", True)
        offline_rows = store.query_recent(event_name="device.offline", limit=5)
        return {
            "name": "offline_command",
            "status": result["status"],
            "reason": result.get("reason"),
            "offline_log_count": len(offline_rows),
        }
    finally:
        await gateway.close()
        await store.drain_and_close()


async def scenario_timeout_unknown(db_path: Path) -> dict:
    gateway = Gateway(Settings(tcp_port=0, command_timeout=0.08))
    store = EventLogStore(db_path)
    attach_event_log(gateway, store)
    await store.start()
    await gateway.start()
    host, port = gateway.address[:2]
    reader, writer = await asyncio.open_connection(host, port)
    try:
        await send_message(
            writer,
            make_message("REGISTER", {"device_type": "light", "state": {"power": False}}, device_id="silent"),
        )
        await read_message(reader)
        result = await gateway.submit_command("silent", "set_power", True)
        await asyncio.sleep(0.05)
        retries = store.query_recent(event_name="command.timeout_retry", limit=10)
        completed = store.query_recent(event_name="command.completed", limit=5)
        return {
            "name": "timeout_unknown",
            "status": result["status"],
            "reason": result.get("reason"),
            "timeout_retry_logs": len(retries),
            "completed_logs": len(completed),
        }
    finally:
        await close_writer(writer)
        await gateway.close()
        await store.drain_and_close()


async def scenario_udp_report(db_path: Path) -> dict:
    gateway = Gateway(Settings(tcp_port=0, udp_port=0))
    store = EventLogStore(db_path)
    attach_event_log(gateway, store)
    await store.start()
    await gateway.start()
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        asyncio.DatagramProtocol,
        remote_addr=gateway._udp.get_extra_info("sockname"),
    )
    sensor = Sensor("sensor_1")
    try:
        transport.sendto(encode_datagram(sensor.next_report()))
        await asyncio.sleep(0.05)
        states = store.query_recent(event_name="device.state", device_id="sensor_1", limit=5)
        return {
            "name": "udp_report",
            "device_online": gateway.registry.get("sensor_1") is not None,
            "state_log_count": len(states),
        }
    finally:
        transport.close()
        await gateway.close()
        await store.drain_and_close()


SCENARIOS = {
    "multi_device": scenario_multi_device,
    "offline_command": scenario_offline_command,
    "timeout_unknown": scenario_timeout_unknown,
    "udp_report": scenario_udp_report,
}


async def main_async(db: Path, only: list[str] | None) -> list[dict]:
    names = only or list(SCENARIOS)
    results = []
    for name in names:
        fn = SCENARIOS.get(name)
        if fn is None:
            raise SystemExit(f"unknown scenario: {name}")
        path = db.parent / f"{name}_{db.name}"
        path.unlink(missing_ok=True)
        results.append(await fn(path))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run member-4 system scenarios")
    parser.add_argument("--db-dir", default="data/scenarios", help="Directory for per-scenario sqlite files")
    parser.add_argument("--scenario", action="append", dest="scenarios", help="Run one scenario (repeatable)")
    args = parser.parse_args()
    db_dir = Path(args.db_dir)
    db_dir.mkdir(parents=True, exist_ok=True)
    summary = asyncio.run(main_async(db_dir / "events.sqlite", args.scenarios))
    print(json.dumps({"scenarios": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()