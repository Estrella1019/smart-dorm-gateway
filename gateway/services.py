"""Public service contract for a future HTTP/WebSocket adapter.

The bootstrap implementation is Gateway; member 2 may extract control logic here.
"""

from typing import Protocol


class GatewayServices(Protocol):
    def list_devices(self) -> list[dict]: ...

    async def submit_command(self, device_id: str, action: str, value: bool) -> dict:
        """Return {request_id, status, state?, reason?}; status may be unknown."""
        ...
