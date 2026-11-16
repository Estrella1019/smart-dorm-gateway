# Integration status and demonstration

This branch integrates the team modules. Main remains unchanged.

## Run (Python 3.11+)

From the repository root, install `python -m pip install -r requirements.txt`.
Run each command in a separate terminal:

```
python -m web.server --config config.example.toml
python -m devices.light --config config.example.toml
python -m devices.door_lock --config config.example.toml
python -m devices.sensor --config config.example.toml
```

Open http://127.0.0.1:8080. The web launcher starts the TCP/UDP gateway too;
do not start gateway.server alongside it. Verify light on/off, lock/unlock,
and changing temperature/humidity. Stop a device and check offline status;
restart it and check recovery. Sensors expire after 15 seconds without reports.

## Leader integration changes

- TCP registration, state validation and command routing support light/power/set_power
  and door_lock/locked/set_locked, using the existing retry and queue implementation.
- UDP 9001 accepts raw JSON REPORT messages from sensor simulators (no TCP prefix).
  Values are validated, registered and published through the shared event bus.
- Per-device session_id and seq reject repeated/out-of-order reports and retired sessions.
  At most 128 retired sessions per sensor are retained per gateway lifetime; further
  restarts are rejected until gateway restart. Sender identity is not authenticated;
  this remains a trusted local classroom simulator, not a production gateway.
- TCP/UDP identity collisions are rejected. Sensor count is bounded by max_connections.
- Heartbeat expiry closes the old socket; old sessions cannot restore themselves.
  Defaults are 5-second device heartbeat and 15-second gateway expiry.
- Only a live pending command result may update state; stale replies are ignored.
- Shutdown cancels client handlers before waiting for the server to close, fixing
  the restart test hang. UDP and heartbeat tasks are closed too.

## Verification

`python -m unittest discover -s tests -v`

Includes real TCP door control, expiry/reconnect, UDP reporting/reordering/restarts,
identity collision protection, stale replies, existing device tests and HTTP/WebSocket
light tests. Queuing tests use a generous confirmation timeout independently of the
finite-retry test, so scheduler delays do not turn retries into false queue failures.
Browser visual acceptance, multi-machine checks, persistent logging and load testing
remain separate acceptance work.
