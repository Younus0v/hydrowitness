# Failure modes and effects: the Witness Node (and what the system does about each)

An FMEA asks, for every part, "how can it fail, what happens, how would we know, what do we do?" This one is written by students from our own build notes and bench logs. **It has not been reviewed by a reliability or safety engineer, and nobody has scored it from field data.** The severity and likelihood columns are our judgement, kept coarse on purpose (H, M, L); they are there to decide what to test first, not to certify anything.

The node's states are `R` (running), `S` (stopped) and `U` (unknown) for each channel (vibration, sound) and combined. The server turns a silent or unknown node into "trust cannot be green" and logs it. The node never controls anything.

**Evidence column:** *bench* = seen on the real ESP32-S board in `measurements/` (no pump); *sim* = covered by the simulated node and the firmware host tests on a computer; *none* = no test yet.

| # | part | failure mode | effect on the system | how it is noticed | what we did | sev | likelihood (our guess) | evidence |
|---|---|---|---|---|---|---|---|---|
| 1 | Accelerometer module | Clone chip (WHO_AM_I `0x72`, not `0x68`) behaves differently | Wrong sample rate or scale; vibration bands off | Boot warning; `acc_rate` in status | Firmware accepts the clone, sets 184 Hz low-pass so the rate reads 999.0 to 999.4 Hz with 0 overflows; calibrates per unit | M | seen once | bench |
| 2 | Accelerometer | Not found, or bus errors | Vibration channel `U`; sound channel alone | "accelerometer not usable" at boot; `i2c_errs` | Node reports `U` for that channel; server needs agreement and says so | M | L | bench (7 bus errors in one session, 0 in the next) |
| 3 | Accelerometer | FIFO overflow (sample rate too high) | Garbled spectrum | `acc_overflows` | Fixed by the low-pass setting above | M | seen on 26 Oct, fixed 28 Oct | bench |
| 4 | Microphone (I2S) | Does not start; wrong left/right slot; silence | Sound channel `U` or flat | "microphone could not start"; `mic_errs` | `MIC_USE_RIGHT_SLOT` setting; `U` on failure | M | L | bench (`mic_errs=0`); **not tested with a sound source** |
| 5 | Microphone | Noise from other equipment, rain, a fan | False `R` on the sound channel | Disagreement with vibration or with SCADA | Two channels must agree for a firm verdict; consistency rules treat disagreement as amber | M | M | sim only |
| 6 | Mounting | Box loose, or on the wrong surface; vibration isolated | `S` while pump runs (a false stop) or `U` | Disagreement with SCADA and customer data | Mounting guidance in `docs/WIRING.md`; install calibration must be done with the pump off, then on; MARGINAL/FAIL bands in `HARDWARE_STAGE.md` | H | M | **none: no pump recording exists** |
| 7 | Mounting | Node sits on a neighbouring pump or motor | Wrong pump reported | Mismatch with SCADA pump status | Install guide says one node per pump and to label it; no automatic check | M | M | none |
| 8 | Calibration | Done with the pump running by mistake | Baseline for "off" is wrong; verdicts invert | Verdict contradicts SCADA at first use | Lights and button guidance; the node prints the calibration verdict (GOOD / not good); calibration needs the pump off by procedure | H | M | sim; start of calibration seen on the bench; **never run to the end** (needs a pump or motor) |
| 9 | Calibration | Out of memory | Calibration cannot start | "calibration could not start: not enough memory" | Seen on 28 Oct; fixed by a smaller request (about 15 kB of the ~32 kB usable) | L | fixed | bench |
| 10 | Wi-Fi | Not joined, or dropped | No messages; node silent | Server marks silent after 3 heartbeats; trust cannot be green | Node retries; silence is logged | M | M | bench (joins, rssi -59 to -74); server link **not tested** |
| 11 | Clock (NTP) | Not set after a reset | Node sends nothing until the time is set | Node silent for a while after boot | By design: signed messages need a valid timestamp; the server refuses stale or skewed ones | L | M | bench (`time=0` after reset, `time=1` later) |
| 12 | Server | Unreachable | Node has nowhere to report | Node shows `sent=0`; server sees silence | **The node does not buffer events while offline** (listed as not included) | M | M | bench (`sent=0`) |
| 13 | Flash storage | Boot counter lost or reset | Server rejects messages as replays until re-enrolled | `rejected` count, evidence log | Documented in `firmware/README.md`; re-enrol the node | M | L | sim |
| 14 | Secret | Wrong or mismatched secret | Every message rejected (`bad_signature`) | Rejections in the dashboard and log | Same 64-hex secret must be in `secrets.h` and the server file; test in host tests | L | M | sim |
| 15 | Secret | Extracted from flash by someone with the board | Attacker can speak as that node | Not noticed | **Flash encryption and secure boot not enabled.** One secret per node limits the damage | H | L | none |
| 16 | Power | Brown-out or reset | Node restarts, boot counter rises | Boot count in message | Counted by `b` in the wire format | L | M | bench (boot 14 seen) |
| 17 | Power | Plant loses mains | Node may lose power too, or run on backup | Silent node; `p`=0 only if the mains-sense circuit is built | Mains-sense is optional and not built; "power outage lookalike" drill uses it in simulation | M | M | sim only |
| 18 | Environment | Moisture, heat, dust in a pump room | Corrosion, drift, sensor failure | Rising `i2c_errs`, silence | Enclosure advice only; **no environmental test** | M | M | none |
| 19 | Classifier | Thresholds fit to a different pump | Wrong `R`/`S` for this pump | Verdict contradicts SCADA | Per-install calibration; V3 gate in `HARDWARE_STAGE.md` (held-out time periods, worst fold 90% or better) | H | unknown | sim and synthetic only; **real pump: not measured** |
| 20 | Clone scale | Accelerometer reads 1.159 g at rest | Features scaled wrongly | Seen in bench probe | Per-unit calibration; left alone and written down | L | seen once | bench |
| 21 | Attacker | Plays a recording of a running pump near the box | False `R` on sound | Vibration channel disagrees | Two channels; not tested against a recording | M | L | none |
| 22 | Buzzer / LEDs | Fail or are misread | Operator misses a local status | None | Three independent lights plus the dashboard | L | L | bench (button, blink and beep worked 28 Oct) |

## What this table says in one paragraph

The failures we have actually seen were all on the bench and all in the board and firmware, and each was fixed. Every row that depends on a **real pump** (6, 7, 8, 19, 21) has no real evidence, and those are the rows with the highest severity. That is why `docs/PUMP_TEST_PROTOCOL.md` exists, and why the website says the pump trial is reported but unrecorded and no data from it is claimed.

## Next actions, in order

1. Record the pump test as written in `docs/PUMP_TEST_PROTOCOL.md` (rows 6, 8, 19).
2. Run the install calibration to the end on a real pump (row 8).
3. Add an on-node buffer so offline events are not lost (row 12), or state it as a limit to every site.
4. Decide whether flash encryption is worth the loss of easy re-flashing for the intended sites (row 15).
5. Ask an engineer or a technician with plant experience to read this table and add the failures we have not thought of.
