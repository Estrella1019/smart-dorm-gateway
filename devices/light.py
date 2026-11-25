"""One simulated light. No hardware required."""

from devices.base import TcpDevice, run_device_cli


class Light(TcpDevice):
    device_type = "light"
    state_field = "power"

    def __init__(self, device_id: str):
        super().__init__(device_id)
        self.power = False

    def state(self) -> dict:
        return {"power": self.power}

    def load_state(self, state: dict) -> None:
        self.power = self._boolean_field(state)

    def execute(self, action: object, value: object) -> bool:
        if action == "set_power" and type(value) is bool:
            self.power = value
            return True
        return False


async def run_light(host: str, port: int, device_id: str, heartbeat_interval: float = 5.0,
                    report_interval: float = 0.0) -> None:
    await Light(device_id).run(host, port, heartbeat_interval, report_interval)


def main() -> None:
    run_device_cli(Light, "Software-only light simulator", "light_001")


if __name__ == "__main__":
    main()
