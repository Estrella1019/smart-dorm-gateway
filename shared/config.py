"""Read a public, non-secret TOML configuration (Python 3.11+)."""

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    tcp_port: int = 9000
    command_timeout: float = 3.0
    first_message_timeout: float = 5.0
    max_connections: int = 128


def load_settings(path: str | None = None) -> Settings:
    if path is None:
        return Settings()
    with Path(path).open("rb") as stream:
        data = tomllib.load(stream)
    if set(data) - {"gateway"}:
        raise ValueError("unknown configuration section")
    section = data.get("gateway", {})
    if not isinstance(section, dict):
        raise ValueError("gateway configuration must be a table")
    try:
        result = Settings(**section)
    except TypeError as exc:
        raise ValueError("unknown gateway configuration key") from exc
    if not isinstance(result.host, str) or not result.host:
        raise ValueError("host must be a non-empty string")
    if type(result.tcp_port) is not int or not 1 <= result.tcp_port <= 65535:
        raise ValueError("tcp_port must be 1..65535")
    for name in ("command_timeout", "first_message_timeout"):
        value = getattr(result, name)
        if type(value) not in (int, float) or not 0.1 <= value <= 120:
            raise ValueError(f"{name} must be 0.1..120 seconds")
    if type(result.max_connections) is not int or not 1 <= result.max_connections <= 10000:
        raise ValueError("max_connections must be 1..10000")
    return result
