# Wiring the Witness Node

Board: **ESP32 DevKit V1 (30-pin)**. Everything below runs from 3.3 V except the optional buzzer transistor.
The node only listens. There is no connection to any pump, valve, PLC or control wiring, and there must never be one.

## Parts

| part | notes |
|---|---|
| ESP32 DevKit V1, 30-pin | the 38-pin boards have the same GPIO numbers; check the silkscreen, not the pin position |
| GY-521 (MPU6050) | accelerometer, I2C. Leave AD0 unconnected (address 0x68). If the node prints `WHO_AM_I is 0x72`, the module carries a different chip: see "Stage 7 progress" in `docs/HARDWARE_STAGE.md` |
| INMP441 | I2S MEMS microphone breakout |
| 6 mm tactile button | one |
| LEDs: green, amber, red + three 330 ohm resistors | |
| active piezo buzzer | **active** (has its own oscillator), 3.3 V or 5 V type |
| NPN transistor (2N2222 or BC547) + 1 kohm resistor | only if the buzzer draws more than about 10 mA; most do |
| ABS project box, metal hose clamp | the clamp holds the box against the motor or pump housing |
| USB power: a wall adapter, optionally with a power bank behind it | |

## Connections

| signal | ESP32 pin | goes to |
|---|---|---|
| I2C SDA | GPIO21 | GY-521 SDA |
| I2C SCL | GPIO22 | GY-521 SCL |
| 3.3 V, GND | 3V3, GND | GY-521 VCC, GND |
| I2S bit clock | GPIO14 | INMP441 SCK |
| I2S word select | GPIO15 | INMP441 WS |
| I2S data in | GPIO32 | INMP441 SD |
| 3.3 V, GND | 3V3, GND | INMP441 VDD, GND |
| channel select | | INMP441 **L/R to GND** (left slot) |
| button | GPIO4 | other side of the button to GND (internal pull-up is used) |
| green LED | GPIO25 | through 330 ohm to the LED, other leg to GND |
| amber LED | GPIO26 | same |
| red LED | GPIO33 | same |
| buzzer | GPIO27 | see below |
| mains sense (optional) | GPIO34 | see below |

Keep the I2S wires short (under about 15 cm) and away from the Wi-Fi antenna end of the board. Do not use GPIO12
for anything: it is a boot-strapping pin. GPIO34 is input-only, which is what the mains sense needs.

**Buzzer.** Direct: GPIO27 to the buzzer's + leg, the other leg to GND, only if it draws under about 10 mA.
Otherwise: GPIO27 through 1 kohm to the transistor's base, emitter to GND, buzzer between 3.3 V (or 5 V from VIN
if the buzzer is a 5 V type) and the collector.

**Mains sense (optional; `HAVE_MAINS_SENSE` is 0 until you build it).** This lets the node tell a power outage
from an attack: the server shows "backup power" instead of raising an alarm. It is a small addition to the
original parts list, to be confirmed with the hardware lead before it is built:

```
wall adapter 5 V ---+---|>|---+--- ESP32 VIN (5 V rail)       |>| = Schottky diode, e.g. 1N5819
                    |         |
                    |   power bank 5 V ---|>|---+
                    |
                    +--[100k]--+--[100k]-- GND
                               |
                               +--- GPIO34
```

The sense point is on the wall-adapter side of its diode, so it falls to zero when mains is lost even though the
power bank keeps the node alive. With two equal resistors GPIO34 sees 2.5 V when 5 V is present. The firmware
reads it, doubles it, and reports mains lost when the rail stays under `MAINS_LOST_MV` (3500) for 3 s. Then set
`HAVE_MAINS_SENSE 1` in `config.h`. The ESP32's ADC is only roughly accurate (several percent), which is plenty for
"5 V" versus "0 V".

## Mounting (this decides whether the whole node works)

1. **Accelerometer:** it must feel the machine. Clamp the box to bare metal on the motor's end bell or the pump
   housing, as close to the bearings as you can, with the hose clamp pulled tight. Paint, plastic shrouds and
   loose sheet metal all swallow vibration. The board must not rattle in the box: foam or hot glue it down. Any
   orientation works (the three axes are used separately).
2. **Microphone:** drill a 4 mm port in the box wall facing the pump and put the INMP441's hole behind it, with a
   thin foam ring so sound goes in and wind does not. Keep it away from the box's own LEDs and buzzer (it should
   hear the pump, not itself).
3. **Away from the wall:** a node pressed against a wall hears the building's other machines too.
4. **One node per pump,** each with its own key and its own calibration.
5. Install only with the permission of the system's operator, and never open or touch an energized motor
   enclosure. The clamp goes on the outside of the housing.

## What the lights and the buzzer mean

| you see | it means |
|---|---|
| green steady | the node thinks the pump is **running** |
| red steady | the node thinks the pump is **stopped** (normal about half the time, not an alarm) |
| amber steady | the node is unsure: not enough agreement between its two senses |
| amber blinking slowly (1 s on, 1 s off) | **not calibrated**: needs the install procedure |
| the pump light plus a brief amber blip every 2 s | the node cannot reach the server; it keeps listening |
| amber flashing twice a second | the node is unsure **and** cannot reach the server |
| red blinking fast | calibration step 1: recording the pump **off** |
| green blinking slowly | calibration: **start the pump now**, wait until it is steady, then press the button |
| green blinking fast | calibration step 2: recording the pump **on** |
| three green flashes, one long beep | calibration finished, both senses good |
| three amber flashes, two beeps | finished, but one sense is weak or missing; the node works at reduced confidence |
| three red flashes, three beeps, then red/amber alternating for ten seconds | calibration failed: the node cannot tell the pump on from off where it is mounted. **Move it** and repeat |
| all three lights flash once together | you confirmed the node's reading (it adapts slightly) |
| amber blinks twice | the node refuses a confirmation because it is not sure |
| one chirp | the pump state just changed |
| three short beeps every 15 s | the **server** says red. A short button press silences it for ten minutes |

Button: **hold 3 s** to start (or cancel) calibration; **short press** to confirm the lights are right, to silence an
alarm, or to say "the pump is running now" during calibration. Red and amber alternating every quarter
second, forever, is a fatal configuration error (placeholder secret, no sensor found, no memory): read the serial monitor.
