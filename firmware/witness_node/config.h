// Witness Node settings. Pin numbers match docs/WIRING.md (ESP32 DevKit V1, 30-pin).
// Behaviour constants (calibration length, long-press time, mute time) live in hw_node.h; change them there
// only together with tests/test_firmware_node.py.
#pragma once

// ---- identity and server (the secret itself lives in secrets.h, never here)
#define NODE_ID            "NB"          // must match this node's id in the server's site profile (max 16 characters)
#define MAINS_HZ           60            // 50 or 60: which supply frequency's hum the node watches
#define HEARTBEAT_S        10            // one heartbeat per this many seconds; state changes go out immediately
#define SERVER_URL         "http://192.168.50.10:8000/api/node"   // the HydroWitness server's node endpoint
#define USE_TLS            0             // 1: https, trusting only SERVER_CA_PEM from secrets.h (untested, see README)
#define NTP_SERVER         "pool.ntp.org"  // on an isolated network, point this at a local NTP server

// ---- pins
#define PIN_I2C_SDA        21            // GY-521 / MPU6050
#define PIN_I2C_SCL        22
#define PIN_I2S_SCK        14            // INMP441 SCK (bit clock)
#define PIN_I2S_WS         15            // INMP441 WS (word select)
#define PIN_I2S_SD         32            // INMP441 SD (data out)
#define PIN_BUTTON         4             // 6 mm tactile switch to GND (internal pull-up)
#define PIN_LED_GREEN      25            // pump seems RUNNING
#define PIN_LED_AMBER      26            // unsure, needs calibration, or lost the server
#define PIN_LED_RED        33            // pump seems STOPPED
#define PIN_BUZZER         27            // active piezo buzzer (add a transistor if it draws more than ~10 mA)
#define PIN_MAINS_SENSE    34            // ADC1 input, only read when HAVE_MAINS_SENSE is 1

// ---- options
#define MIC_USE_RIGHT_SLOT 0             // INMP441 with L/R tied to GND is the LEFT slot; set 1 if the mic reads silence
#define HAVE_MAINS_SENSE   0             // 1 only after the divider in docs/WIRING.md is built; otherwise power is reported OK
#define MAINS_LOST_MV      3500          // wall-side 5 V rail (after undoing the divider) below this = mains lost
#define MAINS_LOST_HOLD_MS 3000          // ...for at least this long
