# Smart Dorm Gateway

A software-only smart dormitory gateway for exploring device connectivity, application-layer protocols, and remote control with Python.

Smart Dorm Gateway brings simulated dormitory devices behind a common gateway. Its goal is to support TCP-connected lights and door locks, UDP-based environmental sensors, and a web interface for monitoring and control. All devices run as software processes, so the project can be developed and demonstrated without physical IoT hardware.

**Current implementation:** an asynchronous TCP gateway, a simulated light, and a command-line client. Device registration, state queries, power commands, and execution acknowledgments work end to end. UDP connectivity, additional device types, and the web interface are planned extensions.

## Overview

The project explores how a gateway translates user actions into device commands and turns device messages into a consistent view of the system. It focuses on:

- Receiving complete application messages over a TCP byte stream.
- Managing multiple connections and routing commands to the correct device.
- Correlating execution results with individual requests.
- Distinguishing confirmed results from operations whose outcomes are unknown.
- Separating transport, device state, control services, and event consumers.

The implementation is intended for networking coursework and local experimentation. One computer can run the gateway and multiple simulated devices; devices can also connect from separate computers on a controlled local network.

## Features

### Available now

- **Asynchronous TCP server:** handles multiple connections with `asyncio`.
- **Framed JSON protocol:** uses a four-byte, network-order length prefix followed by UTF-8 JSON, with message size and field validation.
- **Device registration and discovery:** tracks connected lights and exposes their latest reported state through the CLI.
- **Remote light control:** sends explicit on/off commands and returns the device's execution result.
- **Request correlation:** gateway-generated request IDs associate acknowledgments with the original device session.
- **Basic failure handling:** reports offline or busy devices, rejects duplicate active device IDs, and returns an unknown outcome on confirmation timeout or disconnection during a command.
- **Extension interfaces:** service contracts and an event bus provide integration points for monitoring and persistence.
- **Automated tests:** exercise protocol framing and real loopback TCP communication.

### Planned extensions

- UDP temperature and humidity sensors, plus a TCP door-lock simulator.
- Heartbeat-based offline detection and automatic device reconnection.
- Bounded command queues, retries, and device-side duplicate detection.
- HTTP control APIs, WebSocket updates, and a browser-based dashboard.
- Persistent device and command logs, with repeatable load and fault-injection experiments.

These extensions are not included in the current runnable version.

## Architecture

### Current system

```text
Command-line client
        |
        | TCP: list devices or submit a command
        v
Gateway
  +-- Message framing and validation
  +-- Device registry and connection routing
  +-- Command submission and result correlation
  +-- Event notifications
        |
        | Persistent TCP device connection
        v
Simulated light
  +-- In-memory power state
  +-- Command execution and acknowledgment
```

For a control operation, the gateway assigns a request ID and sends the command to the registered light. The light updates its state and replies with that ID. The gateway then updates its device snapshot and returns the result to the client.

### Target system

```text
Simulated lights and door locks -- TCP --+
                                        |
Simulated environmental sensors -- UDP --+-- Gateway -- HTTP/WebSocket -- Web dashboard
                                               |
                                         Persistent logs
```

The target architecture adds protocol adapters and user interfaces around the control services. It does not require physical sensors or actuators.

## Quick start

### Requirements

- Python **3.11 or later**.
- No third-party Python packages are required for the current implementation.

Clone the repository if you do not already have a local copy:

```bash
git clone https://github.com/Estrella1019/smart-dorm-gateway.git
cd smart-dorm-gateway
```

Run all commands below from the repository root. On Windows, use `py -3` in place of `python3` if needed.

### 1. Start the gateway

In the first terminal:

```bash
python3 -m gateway.server
```

By default, it listens on `127.0.0.1:9000`. Keep this terminal running.

### 2. Connect a simulated light

In a second terminal, open the same repository directory and run:

```bash
python3 -m devices.light --device-id light_001
```

The light registers with the gateway and starts with its power state set to `false`. Keep this terminal running as well.

### 3. Query and control the device

In a third terminal, from the repository root:

```bash
# List connected devices.
python3 -m client.cli list

# Turn the simulated light on, then verify its state.
python3 -m client.cli power light_001 on
python3 -m client.cli list

# Turn it off, then verify again.
python3 -m client.cli power light_001 off
python3 -m client.cli list
```

A successful power-on response has this structure; the actual request ID is generated for each command:

```json
{
  "version": 1,
  "type": "COMMAND_RESULT",
  "payload": {
    "status": "success",
    "state": {
      "power": true
    }
  },
  "device_id": "light_001",
  "request_id": "<gateway-generated-request-id>"
}
```

To connect another light, open an additional terminal and use a different device ID:

```bash
python3 -m devices.light --device-id light_002
```

Stop a simulator or the gateway with `Ctrl+C` in its terminal. Disconnected devices are removed from the current registry. Restarting a light resets its in-memory state to off.

## Configuration

Without `--config`, all programs use the built-in defaults. To customize them, copy `config.example.toml` to `config.local.toml`, which is excluded from Git:

```toml
[gateway]
host = "127.0.0.1"
tcp_port = 9000
command_timeout = 3.0
first_message_timeout = 5.0
max_connections = 128
```

| Setting | Purpose |
|---|---|
| `host` | Server bind address; also the default gateway address for clients |
| `tcp_port` | TCP port used by the gateway and clients |
| `command_timeout` | Seconds to wait for an execution acknowledgment after sending a command |
| `first_message_timeout` | Seconds allowed for the first message on a new connection |
| `max_connections` | Maximum number of concurrently handled TCP connections |

Pass the configuration explicitly:

```bash
python3 -m gateway.server --config config.local.toml
python3 -m devices.light --config config.local.toml --device-id light_001
python3 -m client.cli --config config.local.toml power light_001 on
```

CLI options such as `--config` and `--host` must appear before the `list` or `power` subcommand.

### Connecting over a local network

Set the gateway's bind address to `0.0.0.0` in its local configuration, then start it with that configuration. On other computers, connect using the gateway computer's actual LAN IP address:

```bash
python3 -m devices.light --host <gateway-lan-ip> --device-id light_001
python3 -m client.cli --host <gateway-lan-ip> list
```

Replace `<gateway-lan-ip>` before running these commands. If you change the TCP port, provide a matching configuration on the client computers. Allow the port through the host's firewall as needed. `0.0.0.0` is a bind address, not a destination address for clients.

## Protocol and command outcomes

Each TCP frame contains:

```text
4-byte unsigned length, big-endian | UTF-8 JSON message
```

The length counts JSON bytes, not characters. Message bodies are limited to 64 KiB. Common fields include `version`, `type`, and `payload`, with `device_id` and `request_id` where required.

The current flow uses `REGISTER`, `REGISTERED`, `LIST_DEVICES`, `DEVICES`, `COMMAND`, `COMMAND_RESULT`, and `ERROR`. The server also accepts `REPORT` and `HEARTBEAT` from registered devices, but periodic heartbeat generation and expiry handling are not yet implemented.

| Outcome | Meaning |
|---|---|
| `success` | The device confirmed successful execution |
| `failed` | The request was rejected, or the device reported failure |
| `unknown` | No confirmed result is available, for example after a timeout or disconnection during the operation |

A timeout does **not** prove that a device failed to execute a command. TCP delivery and application-level execution confirmation are separate concerns.

For CLI automation, exit code `0` indicates a completed list query or successful command, `1` indicates a protocol/connection error, and `2` indicates a command result other than success.

## Repository structure

```text
smart-dorm-gateway/
├── shared/                 # Wire protocol and configuration
├── gateway/                # TCP server, registry, events, and service contracts
├── devices/                # Software device simulators
├── client/                 # Command-line control client
├── web/                    # Placeholder for the future web interface
├── tests/                  # Protocol, configuration, and TCP integration tests
├── docs/                   # Architecture, protocol, and development documentation
├── .github/workflows/      # Automated test workflow
├── config.example.toml     # Public configuration example
└── README.md
```

## Testing

Run the test suite from the repository root:

```bash
python3 -m unittest discover -s tests -v
```

Integration tests start real TCP servers on dynamically assigned loopback ports. No separately running gateway is required. Coverage includes fragmented and coalesced messages, invalid frames, light control, confirmation timeouts, disconnect cleanup, duplicate registration, and acknowledgment/session matching.

The GitHub Actions workflow is configured to run the suite on Python 3.11–3.14. See the repository's Actions tab for the latest CI results.

## Current limitations

- Only the TCP light simulator is available; there is no UDP listener or running web service yet.
- The registry retains connected devices only. Heartbeat expiry and offline history are not implemented.
- Overlapping commands to the same device are rejected with `device_busy`; there is no command queue or automatic retry.
- Device state and command tracking are in memory. Reconnection and duplicate-execution protection are not implemented.
- The event bus is an in-process integration interface, not a durable message queue.
- Authentication and TLS are not implemented. Run the service on localhost or a controlled LAN, not as a public internet service or a real door-access system.

## Documentation

Additional technical documentation is currently written in Chinese:

- [Architecture and service interfaces](docs/architecture.md)
- [Application-layer protocol](docs/protocol.md)
- [Local verification record](docs/verification.md)

## Contributing

Keep changes focused and include tests for new behavior. Changes to shared message formats, configuration fields, or service contracts should also update the corresponding documentation and examples.

Run the test suite before submitting a pull request. Local configuration, credentials, logs, and generated databases should remain outside version control.
