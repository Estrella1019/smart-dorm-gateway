"""One simulated light. State is in memory; hardware is not required."""

from devices.base import TcpDevice, run_device_cli


class Light(TcpDevice):
    device_type = "light"

    def __init__(self, device_id: str):
        super().__init__(device_id)
        self.power = False

    def state(self) -> dict:
        return {"power": self.power}

    def execute(self, action: object, value: object) -> bool:
        if action == "set_power" and type(value) is bool:
            self.power = value
            return True
        return False


async def run_light(host: str, port: int, device_id: str) -> None:
    await Light(device_id).run(host, port)


def main() -> None:
    run_device_cli(Light, "Software-only light simulator", "light_001")


if __name__ == "__main__":
    main()
