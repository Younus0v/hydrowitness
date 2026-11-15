# Soak and load test of the server

Run on a computer with 2 CPU(s), Python 3.13.18, Linux-6.18.44-fc-v470-x86_64-with-glibc2.39. The server is the real one (uvicorn, the same app `run_local.py` starts), with simulated SCADA, two Witness Nodes and dashboard pages polling it. **These numbers describe this software on that computer. They say nothing about a real plant or a real network.**

1x is a realistic rate: one SCADA update every 5 s, one heartbeat per node every 10 s, one dashboard poll every 2 s. Higher levels multiply it.

## 1x for 30 s (1 dashboard poller(s))

| request | count | median ms | 95th ms | 99th ms | worst ms | answers | connection failures |
|---|---|---|---|---|---|---|---|
| node_NA | 3 | 5.96 | 7.07 | 7.07 | 7.07 | 200: 3 | 0 |
| node_NB | 3 | 5.86 | 8.21 | 8.21 | 8.21 | 200: 3 | 0 |
| scada | 6 | 4.28 | 9.35 | 9.35 | 9.35 | 200: 6 | 0 |
| dashboard_poll | 15 | 3.81 | 7.58 | 7.58 | 7.58 | 200: 15 | 0 |

Server memory: 59 MB at the start of the level, 60 MB at the end, 60 MB peak. Evidence log grew by 1 kB.

## 20x for 30 s (2 dashboard poller(s))

| request | count | median ms | 95th ms | 99th ms | worst ms | answers | connection failures |
|---|---|---|---|---|---|---|---|
| node_NA | 60 | 4.52 | 10.41 | 11.53 | 11.53 | 200: 60 | 0 |
| node_NB | 60 | 4.08 | 9.62 | 9.74 | 9.74 | 200: 60 | 0 |
| scada | 120 | 4.06 | 10.06 | 13.44 | 13.74 | 200: 120 | 0 |
| dashboard_poll | 300 | 4.17 | 8.97 | 11.37 | 13.92 | 200: 300 | 0 |

Server memory: 60 MB at the start of the level, 61 MB at the end, 61 MB peak. Evidence log grew by 1 kB.

## 100x for 30 s (10 dashboard poller(s))

| request | count | median ms | 95th ms | 99th ms | worst ms | answers | connection failures |
|---|---|---|---|---|---|---|---|
| node_NB | 300 | 4.46 | 17.05 | 26.59 | 36.12 | 200: 300 | 0 |
| scada | 600 | 3.9 | 28.54 | 40.09 | 44.25 | 200: 600 | 0 |
| dashboard_poll | 1500 | 24.42 | 36.21 | 39.43 | 43.04 | 200: 1500 | 0 |
| node_NA | 300 | 5.67 | 22.89 | 34.44 | 41.03 | 200: 300 | 0 |

Server memory: 61 MB at the start of the level, 64 MB at the end, 64 MB peak. Evidence log grew by 0 kB.

After the load, the tamper-evident evidence log verified: `{"ok": true, "bad_at": null, "message": "chain intact", "records": 6, "head_hash": "f7441dde3e1b2ef7097f877d3aa0fceadfb856880c64833e3a8650e2f9c11f90"}`. Server still answering: True.
