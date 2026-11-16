"""One simulated door lock. State is in memory; hardware is not required."""

from devices.base import TcpDevice, run_device_cli


class DoorLock(TcpDevice):
    device_type = "door_lock"

    def __init__(self, device_id: str):
        super().__init__(device_id)
        self.locked = True

    def state(self) -> dict:
        return {"locked": self.locked}

    def execute(self, action: object, value: object) -> bool:
        if action == "set_locked" and type(value) is bool:
            self.locked = value
            return True
        return False


async def run_door_lock(host: str, port: int, device_id: str, heartbeat_interval: float = 5.0) -> None:
    await DoorLock(device_id).run(host, port, heartbeat_interval)


def main() -> None:
    run_device_cli(DoorLock, "Software-only door lock simulator", "lock_001")


if __name__ == "__main__":
    main()
