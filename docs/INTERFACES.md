# Interfaces

Everything that crosses a boundary: the Witness Node message, the server API, the files the server reads, and
what it sends out. The code is the authority; this page says where to look.

## 1. Witness Node message

One JSON object per HTTP `POST /api/node`. Heartbeats go out every `HEARTBEAT_S` seconds (default 10); a state
change goes out immediately as an event.

```json
{"n":"NB","b":4,"s":112,"t":1790000123456,"k":"ev","st":"S","c":912,"ca":"S","cm":"S","p":1,"f":"3fa91c02",
 "sig":"<64 hex characters>"}
```

| field | meaning |
|---|---|
| `n` | node id, at most 16 characters. Must exist in the server's secrets file and be assigned to a pump in the site profile |
| `b` | boot counter, kept in the node's flash, +1 at every power-up |
| `s` | sequence number, +1 per message, restarts at 0 each boot |
| `t` | time in milliseconds since 1970 (NTP) |
| `k` | `hb` heartbeat or `ev` event |
| `st` | combined verdict: `R` running, `S` stopped, `U` unknown (`T` transition is accepted by the server; this firmware never sends it) |
| `c` | confidence in permille. Two channels agreeing: 850-975. One channel: 500-760. Channels contradicting: 300. No usable channel: 200 |
| `ca`, `cm` | accelerometer and microphone verdicts, `R`, `S` or `U` |
| `p` | 1 = mains power present, 0 = running on backup. Always 1 unless the mains-sense circuit is built (docs/WIRING.md) |
| `f` | first 8 hex characters of the firmware's MD5, so a changed firmware is visible |
| `sig` | HMAC-SHA256 over the canonical string, hex |

Canonical string (what is signed): `n|b|s|t|k|st|c|ca|cm|p|f`, ASCII, fields joined with `|`, numbers in plain decimal.
Defined in `hydrowitness/security/nodesig.py` (`canonical_string`) and `firmware/witness_node/hw_wire.h`
(`canonical`); `tests/test_firmware_core.py` proves they produce byte-identical output and signatures.

**Key.** 32 random bytes per node. In the firmware's `secrets.h` they are 64 hex characters and the node signs
with the decoded bytes. The server's secrets file is JSON, `{"NB": "<the same 64 hex characters>"}`; a 64-hex
value is decoded to bytes the same way (`parse_secret`), anything else is used as its UTF-8 text (the demo
secrets). Generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`. One key per node, so a
stolen node can be revoked alone.

**What the server checks, in order:** the JSON has these fields with valid types (unknown extra fields are ignored); the node id is known; the
signature matches; `(b, s)` is strictly newer than the last accepted message (replay protection); `t` is within
60 s of the server's clock. Rejections are written to the evidence log and, if repeated, raise an amber
"integrity" signal that can never reach red on its own.

**What a node reply looks like.** Accepted: HTTP 200 `{"accepted": true, "reason": "ok", "trust": "green|amber|red"}`;
the node mirrors `trust` on its buzzer (alarm while red). Rejected: HTTP 403 `{"accepted": false, "reason": "..."}`
with no information about the site.

**Silence is a signal.** No heartbeat for three intervals: the node stops counting as a witness and the dashboard goes amber with "Witness Node ... has gone quiet". A dead or unplugged node is never mistaken for a quiet pump.

**Heartbeat state.** A heartbeat carries the majority of the node's last ten one-second verdicts per channel; an
event carries the verdict that just changed. (This is how `hydrowitness/sim/fake_node.py` models it; the real
firmware sends its current smoothed verdict. Both are consistent with the server.)

## 2. Server API

Start: `HYDROWITNESS_TOKEN=<long random text> uvicorn hydrowitness.api:app --host 0.0.0.0 --port 8000`.
All `/api` routes except `/api/health` and `/api/node` need `Authorization: Bearer <token>` when a token is set.
**Set a token.** Without one every route is open.

| route | purpose |
|---|---|
| `GET /` | the dashboard (one self-contained page, no external requests) |
| `GET /api/health` | liveness, version, whether a token is required, whether the config is the demo one |
| `GET /api/state` | everything the dashboard shows: trust state and why, the Time-to-Trouble clock, witnesses, playbook, alerts |
| `POST /api/scada` | what SCADA currently claims (JSON below). The only way plant data gets in; read-only toward the plant |
| `POST /api/node` | Witness Node messages (authenticated by HMAC, not by the token) |
| `POST /api/manual` | an operator's own reading: `tank_level_ft`, `gauge_psi`, `pump_running`, `flow_confirmed`, `customer_call` |
| `POST /api/ack` | acknowledge an amber or red state |
| `POST /api/playbook/start`, `POST /api/playbook/step/{id}` | the Lockout Playbook |
| `GET/PUT /api/profile` | read or replace the site profile |
| `GET /api/log`, `/api/log/verify`, `/api/log/report`, `/api/log/export` | the hash-chained evidence log: latest records, integrity check, a printable report, newline-JSON export |
| `GET /api/drill/scenarios`, `POST /api/drill/start|stop|control|step|inject` | Drill Mode (runs on a simulated plant in its own core and log; never touches live state) |

`POST /api/scada` body (all fields optional except what you have; `t` defaults to now):

```json
{"t": 1790000100.0, "tank_level_ft": 21.4, "pump_status": {"P1": false, "P2": true},
 "pump_flow_gpm": {"P2": 180.0}, "demand_gpm": 170.0, "pressure_psi": 27.5,
 "commands": [{"t": 1790000090.0, "pump_id": "P2", "action": "START"}]}
```

Getting values out of a real SCADA system is site work and is **not included**: a small read-only script that
polls the plant's historian or an exported tag file and posts this JSON is all the server needs. It must never
write back to the plant.

## 3. Files and environment

| setting | meaning |
|---|---|
| `HYDROWITNESS_PROFILE` | path to the site profile JSON (fields of `SiteProfile` in `hydrowitness/schemas.py`) |
| `HYDROWITNESS_SECRETS` | path to the node secrets JSON described above. Both this and the profile must be set, or the server starts with the **demo** site and says so |
| `HYDROWITNESS_LOG` | path of the evidence log file (append-only JSON lines, hash-chained). Unset = in memory only |
| `HYDROWITNESS_TOKEN` | the access token |
| `NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN` | alerts go only to a private, self-hosted ntfy topic that needs a token. Never a public topic |

Site profile fields that matter most (units: feet, gpm, psi):

| field | meaning |
|---|---|
| `tank_diameter_ft` or `tank_area_sqft`, `tank_height_ft` | the storage tank |
| `tank_floor_elev_ft` | elevation of the tank floor; levels are measured above it |
| `critical_point_elev_ft` | elevation of the service point that would lose pressure first |
| `min_pressure_psi` | the pressure that must hold there (20 by default) |
| `headloss_margin_ft` | **friction loss from the tank to the critical point at peak demand**, from a hydraulic model or a flow test. The expected-case clock scales it down at lower demand (flow^1.852); the worst case uses it as entered |
| `peak_demand_gpm` | the demand the worst case assumes. Real demand above this (main break, fire flow) can make the clock late; the clock says so on screen |
| `typical_demand_gpm_by_hour` | 24 numbers, local hour 0-23 |
| `pumps[]` | `id`, `rated_gpm` (design flow; 80% is credited, and only while a witness shows water moving), `node_id` |
| `utc_offset_hours` | for the hour-of-day demand |
| `pressure_tap_elev_ft` | elevation of SCADA's pressure transmitter, for the static-head consistency rule |

## 4. What the evidence log contains

Every claim, witness, state change, acknowledgement, playbook step and rejected message, each record carrying
the hash of the one before. `/api/log/verify` re-computes the chain and names the first broken record. The log
makes tampering visible; it does not prevent someone with disk access from deleting the whole file.
