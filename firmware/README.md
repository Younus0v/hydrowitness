# Witness Node firmware

A small, read-only listener: an accelerometer on the pump housing and a microphone near it. It learns what
"pump on" and "pump off" look like at its own site, decides which one it hears, and reports to the HydroWitness
server. It never controls anything. Raw audio never leaves the device; only a verdict and a confidence do.

## Status, plainly

* `hw_dsp.h`, `hw_model.h`, `hw_node.h`, `hw_wire.h`, `hw_tables.h` are portable C++ with no Arduino code in
  them. They are compiled and run on a normal computer by `tests/test_firmware_core.py` (numbers match the
  Python reference to 1e-5 dB; HMAC matches RFC 4231; messages are accepted by the real server code) and
  `tests/test_firmware_node.py` (the whole install flow, LEDs, buzzer, button, dead-sensor handling, against
  synthetic pump signals).
* `witness_node.ino` is the hardware glue. It type-checks, with all warnings on, against stand-ins for the
  ESP32 libraries for every `config.h` combination (`tests/test_firmware_node.py`). It has also been **compiled
  for the ESP32 with the real toolchain** (esp32 core 3.3.12, board "ESP32 Dev Module", all warnings on, no
  warnings from our files; also with mains sense, the right mic slot, TLS and 50 Hz switched on). That first
  real compile found one bug, a constant named `EPS` that clashes with a macro in the ESP32's own headers; it
  is now `DSP_EPS`. **It has never been run on a real ESP32**, so I2S, I2C, Wi-Fi and flash behaviour on the
  board are still unproven. Work through `docs/HARDWARE_STAGE.md`, stage 7.

## Build

1. Install the Arduino IDE 2.x, then Boards Manager: **esp32 by Espressif Systems, version 3.0.0 or newer**
   (the sketch refuses to build on the 2.x core). Board: ESP32 Dev Module.
2. Open the folder `firmware/witness_node/` (the sketch name must match the folder name).
3. Copy `secrets.example.h` to `secrets.h` and fill in the Wi-Fi name and password and a **new** node secret:
   `python3 -c "import secrets; print(secrets.token_hex(32))"`. Put the same 64 characters in the server's
   secrets file under this node's id. The sketch refuses to run with the all-zero example secret.
   `secrets.h` is in `.gitignore`; never commit or share it.
4. Edit `config.h`: `NODE_ID` (matches the site profile), `MAINS_HZ` (50 or 60), `SERVER_URL`, and the options
   at the bottom.
5. Upload, open the serial monitor at 115200 baud.

With `arduino-cli`: `arduino-cli compile --fqbn esp32:esp32:esp32 firmware/witness_node`.

## Install at a pump (the operator procedure)

1. Mount the node (`docs/WIRING.md`). Power it. Amber blinks slowly: it is not calibrated.
2. With the pump **off**, hold the button for 3 seconds. Red blinks fast. Do not touch the box for two minutes.
3. Two beeps and a slow green blink: **start the pump**. When it is running steadily, press the button once.
4. Green blinks fast for two minutes. Do not touch the box.
5. The node says how it went (flashes and beeps, table in `docs/WIRING.md`) and prints the verdicts on serial.
   Both channels are scored on data held out from the fit: GOOD at 97% or better, FAIR at 90%, otherwise POOR.
   A POOR channel is switched off. If both are POOR the node stays "not calibrated" and says **move me**.
6. The result is saved in flash and survives power loss.

Holding the button for 3 seconds during any calibration step cancels it and keeps the previous result.
A short press in normal running, when the node is sure (both senses agree), confirms the lights are right and lets
the node's stored picture of "on" or "off" drift slightly toward what it hears now. It never adapts on its own
guess, only after a person confirms. A short press while the alarm is sounding silences it for ten minutes and
does not teach the node anything.

## How it decides (one paragraph)

Every 0.512 s it takes one block from each sense: 512 accelerometer samples at 1 kHz on three axes and 8192
microphone samples at 16 kHz. Two consecutive blocks make a window; each window becomes 15 numbers per sense
(overall level, 8 or 10 frequency bands, the energy at 1x and 2x the mains frequency, how fast the spectrum is
changing, and how peaky the signal is). During calibration it fits, per sense, a two-class model with a shared
variance per feature; afterwards `z` is about +1 for "running" and about -1 for "stopped". A channel's verdict is a
3-of-5 vote over recent windows; the node's verdict changes only after three consecutive agreeing blocks (about
1.5-3.5 s). Both senses agreeing gives confidence 850-975; one sense, 500-760; contradiction, 300; nothing usable,
200. A sensor that reads constant zero is ignored, not believed. All of it is defined once, in
`training/node_reference.py`, and the C++ is tested against it.

## Serial commands (115200 baud)

| command | does |
|---|---|
| `status` | one line of state and counters (also printed every minute) |
| `dump on` / `dump off` | print one `F,<ms>,<label>,<a or m>,<15 numbers>` row per window and sense |
| `label R` / `label S` / `label -` | tag the rows (running / stopped / not labelled), for `training/train_classifier.py` |
| `wipe` | erase the stored calibration and restart (the boot counter is kept) |

Do not erase the whole flash on a node that is already enrolled: the boot counter goes back to 1 and the server
will reject the node's messages as replays until you re-enrol it.

## Settings (`config.h`)

`NODE_ID`, `MAINS_HZ`, `HEARTBEAT_S`, `SERVER_URL`, `USE_TLS`, `NTP_SERVER`, the pins, `MIC_USE_RIGHT_SLOT`,
`HAVE_MAINS_SENSE`, `MAINS_LOST_MV`, `MAINS_LOST_HOLD_MS`. On a network with no internet, run a local NTP server and
point `NTP_SERVER` at it: the node does not send anything until its clock is set, because the server rejects
messages more than 60 s from its own time.

## Memory and timing, for whoever debugs the first build

About 64 KB of heap for the audio and FFT buffers (allocated at boot, before Wi-Fi), about 25 KB of static tables
(window and sine), plus about 31 KB of heap while calibrating; the sense task runs on core 1 at the highest priority, Wi-Fi and the network task on core 0. The
accelerometer's own FIFO collects samples at 1 kHz so timing does not depend on the task being punctual, but the
task has to come back within about 150 ms or the 1024-byte FIFO overflows (counted as `acc_overflows` in `status`).
The microphone's DMA buffers hold 192 ms. A 8192-point FFT is the largest single job; the Hann window is cached
because computing it per sample in double precision would not fit in 512 ms on the ESP32.
