# Integration verification — 21 November 2026

Reviewed and integrated device acceptance (d9be7c0), reliable-control verification
(338002e), web acceptance (cfeab9e) and logging (acddcf9), preserving their commits.

Additional review fixes:
- SQLite connection creation, writes and close use one dedicated executor thread.
  General asyncio.to_thread calls cannot guarantee connection thread affinity.
- Storage startup now awaits database initialization before serving queries.
- HTTP log reads run outside the network event loop.
- Heartbeat concurrency test waits for an actual heartbeat within a bounded deadline,
  rather than assuming a fast command sequence lasts longer than the heartbeat interval.
- Added regression coverage for database readiness and writer thread affinity.

Local verification: macOS, Python 3.14.2, isolated virtual environment.
`python -m unittest discover -s tests -q`: 62 tests passed.
`python scripts/run_scenarios.py`: light and lock succeeded; offline commands rejected;
unconfirmed commands returned unknown with retry logs; UDP state persisted.
The offline scenario checks disconnect rather than heartbeat expiry, so its
zero device.offline rows are not evidence of missing device.disconnected logs.

Remaining delivery work: record the final demo, finalize report/slides and rehearse.
10/50-device performance measurements are not supplied; do not claim load capacity.
Logs have CLI and HTTP queries; a dedicated graphical log viewer is not implemented.
Storage is classroom-scale: its queue is unbounded and insert failures are logged,
not durably retried. Do not describe this as lossless production audit storage.

Multi-process device acceptance script: all 13 checks passed, including gateway
restart, duplicate suppression, delayed/missing acknowledgements, heartbeat expiry
and UDP loss simulation. This is functional verification, not a load benchmark.
