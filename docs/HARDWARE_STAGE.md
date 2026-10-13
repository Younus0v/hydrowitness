# The hardware stage: what needs the real parts, and in what order

Stages 1-6 of the build (shared formats, engines, node verification and evidence log, simulator and drills, API
and dashboard, firmware core and analysis tools) are software and are finished and tested without any hardware.
The three stages below are the only ones that need the parts. Do them in order; each has a gate you can check.

## Stage 7: parts and wiring (gate: the node boots, sees both sensors, and the server accepts its messages)

1. Build the circuit from `docs/WIRING.md`. No pressure sensor is part of this design.
2. Install the Arduino IDE (or arduino-cli) and the **esp32 core by Espressif, version 3.0 or newer**.
   Board: "ESP32 Dev Module". Follow `firmware/README.md` to fill in `config.h` and `secrets.h`.
3. Start a server for the test: `HYDROWITNESS_TOKEN=test-token uvicorn hydrowitness.api:app --host 0.0.0.0 --port 8000`
   (the demo site has nodes `NA` and `NB`; for a real key, put the same 64-hex-character secret in `secrets.h` and in
   the server's secrets file).
4. Flash, open the serial monitor at 115200 baud and type `status`. Check, in this order:

| check | pass |
|---|---|
| boot line | prints node id, boot count, firmware hash, no `FATAL` |
| accelerometer | no "accelerometer not found" line; `acc_rate` after a minute is between 990 and 1010 |
| microphone | no "microphone could not start" line; with `dump on`, tap the box and the mic `F,...,m,...` rows change a lot |
| button and lights | hold the button 3 s: the red light blinks fast; hold 3 s again to cancel |
| Wi-Fi and time | `status` shows `time=1`, and `sent` increases every 10 s with `rejected=0` |
| server | the dashboard shows the node and its last heartbeat; `/api/health` is ok |

If `acc_rate` is outside 990-1010, the accelerometer's own clock is off and the 1x/2x mains-hum bands may sit
slightly outside their windows. Note the number; the other 13 features still work. If the microphone reads
silence, set `MIC_USE_RIGHT_SLOT 1` in `config.h`.

## Stage 8: record on a real pump and measure (gate: validation V3)

The question: on this real equipment, do the node's features separate **on** from **off** for time periods the
model has never seen? Everything else depends on the answer.

1. Mount the node as it will be installed (`docs/WIRING.md`, Mounting). If no water-system pump is available, use
   a real pump or motor of any size (a sump pump, a workshop motor) and say so in the write-up. A fan or a speaker
   playing a recording does not count.
2. Connect USB, start logging the serial output to a file (any terminal program can do that).
3. Type `dump on`. With the pump **off** and quiet, type `label S`; leave it 3-5 minutes. Start the pump, wait
   until it is steady, type `label R`; leave it 3-5 minutes. Type `label -` before every start, stop or touch of the box.
   Repeat the off/on cycle at least three times, and on at least two different days or times of day if you can.
4. Run `python3 training/train_classifier.py session1.log session2.log` and keep the whole printout.
5. Read the result:

| result | meaning | what to do |
|---|---|---|
| PASS | at least one channel separates on from off on unseen time periods, worst fold 90% or better | continue to stage 9 |
| MARGINAL | there is separation, but not reliably in every period | move the node (closer to the end bell, bare metal) and repeat, or log more days |
| FAIL | no channel separates reliably as mounted | try other mounting points first; if none works, that is the finding and goes in the submission as it is |
| NO DATA | not enough labelled windows | log longer |

6. Then do the real install calibration on the node itself (hold 3 s with the pump off, follow the lights). The
   node prints `calibration: accelerometer GOOD (0.98), microphone GOOD (0.99)` or similar. That printout is the
   second piece of V3 evidence.

Record for the write-up: the node's mounting, the pump's size and type, the number of sessions and days, the
train_classifier printout, and the on-device calibration verdicts. Do not summarise a FAIL as a pass.

## Stage 9: the real node replaces the fake node (gate: validation V4)

1. Run the server with the real site profile and secrets (`docs/INTERFACES.md`, section 3), with a token and a
   log file, and point the node's `SERVER_URL` at it.
2. Put the node on a pump the operator has agreed to, and let it run through a few real start/stop cycles. The
   dashboard should follow the pump within a few seconds of each change, and the evidence log should show each change.
3. Repeat the drills with the real node as the witness. Use a **bench pump or motor you control** for anything
   that switches a pump, never the plant's real controls:
   - pump switched off while the screen keeps claiming it runs (the dashboard's Drill Mode shows the screen side
     of this; here the node supplies the real witness);
   - power to the node's mains-sense circuit removed (if built): the dashboard must show backup power, not an alarm;
   - node unplugged: amber ("Witness Node ... has gone quiet") within about 30 s.
4. Measure from the moment of the physical change to amber and to red. Compare with the simulated numbers in
   `results/v2_drills.md`; they are the target, not the result.

## What has not been tested (read this before claiming anything)

| not tested | why it matters | where it gets tested |
|---|---|---|
| the ESP32 sketch itself (I2S, I2C, Wi-Fi, flash storage, HTTP) | it has been type-checked against stand-ins for the ESP32 libraries, never compiled for or run on a real board; the first real build may need small fixes | stage 7 |
| whether real pump vibration and sound separate on from off | all node tests use synthetic signals | stage 8, V3 |
| the accelerometer's actual sample rate | the hum bands assume 1000 Hz | stage 7, `status` |
| HTTPS (`USE_TLS 1`) | built but never exercised; a plain-HTTP node on a private network is the tested path | stage 7, optional |
| the mains-sense circuit | needs two resistors and two diodes; `HAVE_MAINS_SENSE` is 0 until built | stage 9 |
| the node buffering a day of events while offline | **not built**: a node that is offline simply stops being a witness (amber, "has gone quiet") and events older than 45 s are dropped, because the server rejects messages more than 60 s old | would need a backfill endpoint |
| reading a real SCADA system | the server takes JSON on `POST /api/scada`; no plant adapter is included | site work |
| direct-pumping sites (no storage tank) | needs WNTR response curves per site; that tool is not part of this build | not planned |
| the numbers on a real system | every drill and false-alarm figure is from a simulated plant and simulated nodes | a pilot |

Known gaps against the project document, to correct there: the accelerometer bands are 20-250 Hz (the MPU6050's
own low-pass is about 260 Hz), not 20-500 Hz; a careful forgery on its own reaches amber within about 90 s but
reaches red only when an operator looks (around 2 minutes), so the "red within 2 minutes with a node witness"
target holds only with an operator; a valve closed out of sight stays amber (customer reports can never make red
alone, by design); mains-sense is a small addition to the parts list.
