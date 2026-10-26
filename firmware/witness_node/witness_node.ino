// HydroWitness Witness Node: listens to a pump with a cheap accelerometer and microphone and tells the
// HydroWitness server whether the pump seems to be running, independent of SCADA.
//
// This file is only the hardware glue (I2S microphone, I2C accelerometer, Wi-Fi, flash, LEDs). All of the
// signal processing, classification, calibration and operator-button logic is in hw_dsp.h, hw_model.h and
// hw_node.h, which are compiled and tested on a normal computer (tests/test_firmware_core.py and
// tests/test_firmware_node.py). THIS FILE HAS BOOTED ON ONE ESP32-S BOARD (25 Oct 2026) BUT HAS NOT BEEN RUN ON A PUMP,
// AND ITS ACCELEROMETER SET-UP HAS NOT YET BEEN SEEN WORKING ON THE CLONE CHIP THAT BOARD CARRIES. See README.md.
//
// Needs the Arduino-ESP32 core 3.x. Read-only by design: the node only listens and reports. It has no
// output to any pump, valve or control system, and raw audio never leaves the device.
#include <Arduino.h>

#if !defined(ESP_ARDUINO_VERSION_MAJOR) || ESP_ARDUINO_VERSION_MAJOR < 3
#error "Needs the Arduino-ESP32 core 3.x (Boards Manager: 'esp32' by Espressif Systems, version 3.0.0 or newer)."
#endif

#include "config.h"
#include "secrets.h"      // copy secrets.example.h to secrets.h and fill it in

#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <Wire.h>
#include <sys/time.h>
#include <time.h>
#if USE_TLS
#include <WiFiClientSecure.h>
#endif
#include "driver/i2s_std.h"

#include "hw_node.h"

// ------------------------------------------------------------------------------------------ constants
#ifndef I2C_HZ
#define I2C_HZ 400000                                // I2C clock; try 100000 if i2c_errs keeps rising on long jumper wires
#endif
static const uint8_t MPU_ADDR = 0x68;                // GY-521 with AD0 left low
// Accelerometer set-ups tried in order at boot; the first one whose measured FIFO rate is within 10% of 1 kHz is kept.
// A genuine MPU-6050 gives 1 kHz with the first. A clone that ignores the divider gives 1 kHz with the second.
struct MpuCfg { uint8_t dlpf; uint8_t div; const char* name; };
static const MpuCfg MPU_CFGS[] = {
  {0x00, 0x07, "no low-pass, 8 kHz base divided by 8"},
  {0x01, 0x00, "184 Hz low-pass, 1 kHz base divided by 1"},
};
static const int MPU_NCFG = 2;
static const int ACC_SLACK = 192;                    // spare accelerometer samples carried between blocks
static const int MIC_CHUNK = 512;                    // microphone samples per i2s read (32 ms)
static const uint32_t EPOCH_DEADLINE_MS = 1200;      // give up on a block that takes this long (nominal 512 ms)
static const uint32_t MSG_MAX_AGE_MS = 45000;        // the server rejects messages more than 60 s old
static const int WARMUP_EPOCHS = 3;                  // discard the first blocks: the microphone and PLL settle
static const char* NVS_NS = "hwnode";

struct OutMsg {
  char json[320];
  uint32_t born;
};

// ------------------------------------------------------------------------------------------- globals
static hw::NodeLogic logic;
static SemaphoreHandle_t mtx;                        // guards `logic` between the sense, UI and network tasks
static QueueHandle_t txq;
static Preferences prefs;

static float* micbuf = nullptr;                      // 8192 samples; also the real part of the FFT of the mic block
static float* imbuf = nullptr;                       // imaginary scratch for the FFT
static float sre[hw::ACC_N], sim_[hw::ACC_N];        // FFT scratch for the accelerometer blocks
static float accb[3][hw::ACC_N + ACC_SLACK];
static int acc_fill = 0;
static int32_t i2sraw[MIC_CHUNK];
static i2s_chan_handle_t rx_chan = nullptr;

static uint8_t secret[32];
static uint32_t boot_count = 0, seq = 0;
static char fw_hash[17] = "";
static bool mic_present = false, mpu_present = false;
static int mpu_cfg_idx = -1;                         // which MPU_CFGS entry is in use, -1 if none gave 1 kHz
static uint8_t mpu_who = 0;                          // WHO_AM_I as read at the last start
static float mpu_seen_hz[MPU_NCFG] = {0, 0};         // FIFO rate measured with each set-up at boot (negative: faster than 1.7 kHz)
static volatile bool mpu_probe_req = false;          // set by the `mpu` command, handled by the sense task so the I2C bus has one user

// counters, printed by `status`
static uint32_t acc_total = 0, acc_overflows = 0, acc_i2c_errs = 0, acc_i2c_run = 0, mic_errs = 0;
static uint32_t acc_started_ms = 0, msgs_sent = 0, msgs_dropped = 0, msgs_rejected = 0, epochs_done = 0;
static volatile bool dump_on = false;
static volatile char dump_label = '-';

// ------------------------------------------------------------------------------------------- helpers
static void lock() { xSemaphoreTake(mtx, portMAX_DELAY); }
static void unlock() { xSemaphoreGive(mtx); }

// A node that cannot be trusted says so instead of guessing. It never returns (the loop below runs forever).
// Do not mark it [[noreturn]]: the Arduino IDE writes its own declaration of every function in this file
// without that marker, and the compiler then refuses it ("declared [[noreturn]] but its first declaration was not").
static void fatal(const char* why) {
  pinMode(PIN_LED_GREEN, OUTPUT); pinMode(PIN_LED_AMBER, OUTPUT); pinMode(PIN_LED_RED, OUTPUT);
  for (;;) {
    Serial.printf("FATAL: %s\n", why);
    for (int i = 0; i < 10; i++) {
      digitalWrite(PIN_LED_RED, i & 1); digitalWrite(PIN_LED_AMBER, !(i & 1)); digitalWrite(PIN_LED_GREEN, LOW);
      delay(250);
    }
  }
}

static int hexval(char c) { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1; }

static void load_secret() {
  const char* h = NODE_SECRET_HEX;
  if (strlen(h) != 64) fatal("NODE_SECRET_HEX in secrets.h must be exactly 64 hex characters");
  bool all_zero = true;
  for (int i = 0; i < 32; i++) {
    const int hi = hexval(h[2 * i]), lo = hexval(h[2 * i + 1]);
    if (hi < 0 || lo < 0) fatal("NODE_SECRET_HEX in secrets.h has a character that is not hex");
    secret[i] = (uint8_t)(hi * 16 + lo);
    if (secret[i]) all_zero = false;
  }
  if (all_zero) fatal("secrets.h still has the example secret; generate a real one (see secrets.example.h)");
}

static int64_t epoch_ms() {                          // 0 until NTP has set the clock
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  if (tv.tv_sec < 1700000000) return 0;
  return (int64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

static void save_models() {
  hw::Stored s;
  lock(); logic.export_stored(s); unlock();
  prefs.putBytes("model", &s, sizeof(s));
  prefs.putUChar("mains", MAINS_HZ);
  Serial.println("I,models saved");
}

static bool load_models(hw::Stored& s) {
  if (prefs.getBytesLength("model") != sizeof(s)) return false;
  if (prefs.getUChar("mains", 0) != MAINS_HZ) return false;     // hum features depend on the supply frequency
  prefs.getBytes("model", &s, sizeof(s));
  return s.va <= hw::GOOD && s.vm <= hw::GOOD && (s.acc.valid() || s.mic.valid());
}

// ------------------------------------------------------------------------------------------ messages
static void queue_message(const char* kind) {
  const int64_t t = epoch_ms();
  if (t == 0) return;                                // no time yet: the server would reject it anyway
  hw::Wire w;
  memset(&w, 0, sizeof(w));
  strncpy(w.n, NODE_ID, 16);
  w.b = boot_count;
  w.s = ++seq;
  w.t_ms = t;
  strncpy(w.k, kind, 2);
  strncpy(w.f, fw_hash, 16);
  lock(); logic.fill(w); unlock();
  char sig[65];
  hw::sign(w, secret, sizeof(secret), sig);
  OutMsg m;
  hw::to_json(w, sig, m.json, sizeof(m.json));
  m.born = millis();
  if (xQueueSend(txq, &m, 0) != pdTRUE) {            // link down for a long time: drop the oldest, keep the newest
    OutMsg old;
    xQueueReceive(txq, &old, 0);
    msgs_dropped++;
    xQueueSend(txq, &m, 0);
  }
}

// ------------------------------------------------------------------------------------------ network
static char trust_from_body(const String& body) {    // {"accepted":true,"reason":"ok","trust":"red"}
  const int p = body.indexOf("\"trust\"");
  if (p < 0) return 0;
  int q = body.indexOf(':', p);
  if (q < 0) return 0;
  q = body.indexOf('"', q);
  if (q < 0 || q + 1 >= (int)body.length()) return 0;
  const char c = body[q + 1];
  return c == 'g' || c == 'a' || c == 'r' ? c : 0;
}

// Returns the HTTP status, or <= 0 when the server could not be reached.
static int post(const OutMsg& m) {
  HTTPClient http;
#if USE_TLS
  WiFiClientSecure client;
  client.setCACert(SERVER_CA_PEM);
#else
  WiFiClient client;
#endif
  http.setConnectTimeout(3000);
  http.setTimeout(4000);
  if (!http.begin(client, SERVER_URL)) return -1;
  http.addHeader("Content-Type", "application/json");
  const int code = http.POST((uint8_t*)m.json, strlen(m.json));
  if (code == 200) {
    const char tr = trust_from_body(http.getString());
    lock(); logic.note_post(true, tr, millis()); unlock();
  } else if (code > 0) {
    msgs_rejected++;                                 // reachable but refused: wrong secret, replay or clock skew
    Serial.printf("W,server answered %d: %s\n", code, http.getString().c_str());
  }
  http.end();
  return code;
}

static void net_task(void*) {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  configTime(0, 0, NTP_SERVER);
  uint32_t last_try = millis();
  OutMsg m;
  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      if (millis() - last_try > 30000) { WiFi.disconnect(); WiFi.begin(WIFI_SSID, WIFI_PASS); last_try = millis(); }
      vTaskDelay(pdMS_TO_TICKS(500));
      continue;
    }
    last_try = millis();
    if (xQueueReceive(txq, &m, pdMS_TO_TICKS(500)) != pdTRUE) continue;
    int code = 0;
    while (millis() - m.born < MSG_MAX_AGE_MS) {     // retry while the message is still fresh enough to be accepted
      code = post(m);
      if (code > 0) break;
      vTaskDelay(pdMS_TO_TICKS(1000));
    }
    if (code > 0) msgs_sent++; else msgs_dropped++;
  }
}

// ----------------------------------------------------------------------------------------- UI task
static void ui_task(void*) {
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_LED_GREEN, OUTPUT); pinMode(PIN_LED_AMBER, OUTPUT); pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  for (;;) {
    const uint32_t now = millis();
    const bool down = digitalRead(PIN_BUTTON) == LOW;
    lock();
    logic.button(down, now);
#if HAVE_MAINS_SENSE
    logic.mains((int)analogReadMilliVolts(PIN_MAINS_SENSE) * 2, now, MAINS_LOST_MV, MAINS_LOST_HOLD_MS);   // 100k/100k divider
#endif
    const hw::Outputs o = logic.outputs(now);
    unlock();
    digitalWrite(PIN_LED_GREEN, o.g); digitalWrite(PIN_LED_AMBER, o.a); digitalWrite(PIN_LED_RED, o.r);
    digitalWrite(PIN_BUZZER, o.buz);
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

// ------------------------------------------------------------------------------------ accelerometer
static bool mpu_write(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

static bool mpu_read(uint8_t reg, uint8_t* dst, size_t n) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint8_t)MPU_ADDR, (size_t)n) != n) return false;
  for (size_t i = 0; i < n; i++) dst[i] = (uint8_t)Wire.read();
  return true;
}

static void mpu_fifo_reset() {
  mpu_write(0x6A, 0x00);                             // FIFO off
  mpu_write(0x6A, 0x04);                             // FIFO reset
  delay(2);
  mpu_write(0x23, 0x08);                             // FIFO collects the accelerometer only (6 bytes per sample)
  mpu_write(0x6A, 0x40);                             // FIFO on
  acc_fill = 0;
}

static void mpu_apply_cfg(int i) {
  mpu_write(0x1A, MPU_CFGS[i].dlpf);                 // CONFIG: digital low-pass, which also sets the base rate (8 kHz off, 1 kHz on)
  mpu_write(0x19, MPU_CFGS[i].div);                  // SMPLRT_DIV: sample rate = base rate / (1 + this)
}

// The sensor's real sample rate as the FIFO shows it: empty the FIFO, let it fill for 100 ms, count what is in it.
// Returns samples per second, 0 if the bus failed, or -1 if the 1024-byte FIFO had already overflowed, which means the
// sensor runs faster than about 1.7 kHz (this is what the first real board did).
static float mpu_measure_rate() {
  mpu_fifo_reset();
  const uint32_t t0 = micros();
  delay(100);
  uint8_t cnt[2];
  if (!mpu_read(0x72, cnt, 2)) return 0.0f;
  const uint32_t dt = micros() - t0;
  const int bytes = ((int)cnt[0] << 8) | cnt[1];
  if (bytes >= 1000) return -1.0f;
  return (float)(bytes / 6) * 1000000.0f / (float)dt;
}

// measure = true at boot: find a set-up that gives 1 kHz, or give the accelerometer up with a clear message.
// measure = false after a bus restart: reuse the set-up found at boot, so a glitch cannot switch the sensor off for good.
static bool mpu_init(bool measure) {
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, I2C_HZ);
  Wire.setTimeOut(30);
  uint8_t who = 0;
  if (!mpu_read(0x75, &who, 1)) return false;
  mpu_who = who;
  if (measure && who != 0x68) Serial.printf("W,MPU6050 WHO_AM_I is 0x%02x, expected 0x68 (a clone: checking its sample rate)\n", who);
  if (!mpu_write(0x6B, 0x80)) return false;          // reset
  delay(100);
  mpu_write(0x6B, 0x01);                             // wake, clock from the gyro PLL
  mpu_write(0x1C, 0x00);                             // +-2 g, 16384 LSB per g
  if (measure) {
    mpu_cfg_idx = -1;
    for (int i = 0; i < MPU_NCFG && mpu_cfg_idx < 0; i++) {
      mpu_apply_cfg(i);
      mpu_seen_hz[i] = mpu_measure_rate();
      if (mpu_seen_hz[i] >= 900.0f && mpu_seen_hz[i] <= 1100.0f) mpu_cfg_idx = i;
    }
    if (mpu_cfg_idx < 0) {
      mpu_write(0x6A, 0x00);                         // FIFO off
      Serial.printf("W,accelerometer: no setting gives 1 kHz (measured %.0f and %.0f Hz, -1 = faster than 1700): not using it. Type mpu for details\n",
                    (double)mpu_seen_hz[0], (double)mpu_seen_hz[1]);
      return false;
    }
    Serial.printf("I,accelerometer: setting %d (%s) measured %.0f Hz\n", mpu_cfg_idx, MPU_CFGS[mpu_cfg_idx].name, (double)mpu_seen_hz[mpu_cfg_idx]);
  } else {
    if (mpu_cfg_idx < 0) return false;
    mpu_apply_cfg(mpu_cfg_idx);
  }
  mpu_fifo_reset();
  acc_started_ms = millis();
  acc_total = 0;
  return true;
}

// Typed as `mpu`; runs inside the sense task. Prints what the chip says about itself and what it really does, so that a
// clone can be told from a wiring problem. Afterwards the set-up in use is restored.
static void mpu_probe() {
  uint8_t who = 0;
  if (!mpu_read(0x75, &who, 1)) { Serial.println("I,mpu probe: no answer from the accelerometer (check SDA, SCL, power, ground)"); return; }
  Serial.printf("I,mpu probe: WHO_AM_I 0x%02x (a genuine MPU-6050 gives 0x68), I2C clock %d Hz\n", who, (int)I2C_HZ);
  const uint8_t regs[7] = {0x19, 0x1A, 0x1B, 0x1C, 0x23, 0x6A, 0x6B};
  uint8_t r[7] = {0, 0, 0, 0, 0, 0, 0};
  for (int i = 0; i < 7; i++) mpu_read(regs[i], &r[i], 1);
  Serial.printf("I,mpu probe: SMPLRT_DIV 0x%02x CONFIG 0x%02x GYRO_CONFIG 0x%02x ACCEL_CONFIG 0x%02x FIFO_EN 0x%02x USER_CTRL 0x%02x PWR_MGMT_1 0x%02x\n",
                r[0], r[1], r[2], r[3], r[4], r[5], r[6]);
  uint8_t a[6];
  if (mpu_read(0x3B, a, 6)) {
    float g[3];
    for (int i = 0; i < 3; i++) g[i] = (float)(int16_t)(((int16_t)a[i * 2] << 8) | a[i * 2 + 1]) * (1.0f / 16384.0f);
    Serial.printf("I,mpu probe: acceleration x %.3f y %.3f z %.3f g, total %.3f g (about 1.000 when the board is still)\n",
                  (double)g[0], (double)g[1], (double)g[2], (double)sqrtf(g[0] * g[0] + g[1] * g[1] + g[2] * g[2]));
  } else {
    Serial.println("I,mpu probe: could not read the acceleration registers");
  }
  int bad = 0;
  for (int i = 0; i < 200; i++) { uint8_t w = 0; if (!mpu_read(0x75, &w, 1) || w != who) bad++; }
  Serial.printf("I,mpu probe: 200 reads of WHO_AM_I, %d failed\n", bad);
  for (int i = 0; i < MPU_NCFG; i++) {
    mpu_apply_cfg(i);
    const float hz = mpu_measure_rate();
    if (hz < 0) Serial.printf("I,mpu probe: setting %d (%s): FIFO overflowed within 100 ms, faster than 1700 Hz\n", i, MPU_CFGS[i].name);
    else Serial.printf("I,mpu probe: setting %d (%s): %.0f Hz\n", i, MPU_CFGS[i].name, (double)hz);
  }
  if (mpu_cfg_idx >= 0) { mpu_apply_cfg(mpu_cfg_idx); mpu_fifo_reset(); } else { mpu_write(0x6A, 0x00); }
  acc_total = 0;
  acc_started_ms = millis();
  Serial.println("I,mpu probe done");
}

// Pull whatever the sensor's FIFO holds into accb. The sensor clocks its own samples, so timing does not
// depend on this task being on time, only on it coming back before the 1024-byte FIFO fills (about 170 ms).
static void drain_acc() {
  if (!mpu_present) return;
  uint8_t cnt[2];
  if (!mpu_read(0x72, cnt, 2)) {
    acc_i2c_errs++;
    if (++acc_i2c_run >= 20) {                       // the bus is stuck: restart it and the sensor
      acc_i2c_run = 0;
      Wire.end();
      mpu_present = mpu_init(false);
    }
    return;
  }
  acc_i2c_run = 0;
  int bytes = ((int)cnt[0] << 8) | cnt[1];
  if (bytes >= 1000) { acc_overflows++; mpu_fifo_reset(); return; }   // samples were lost: start a fresh block
  int samples = bytes / 6;
  uint8_t buf[30];
  while (samples > 0 && acc_fill < hw::ACC_N + ACC_SLACK) {
    int take = samples < 5 ? samples : 5;
    if (take > hw::ACC_N + ACC_SLACK - acc_fill) take = hw::ACC_N + ACC_SLACK - acc_fill;
    if (!mpu_read(0x74, buf, (size_t)take * 6)) { acc_i2c_errs++; return; }
    for (int i = 0; i < take; i++) {
      for (int a = 0; a < 3; a++) {
        const int16_t v = (int16_t)(((int16_t)buf[i * 6 + a * 2] << 8) | buf[i * 6 + a * 2 + 1]);
        accb[a][acc_fill] = (float)v * (1.0f / 16384.0f);
      }
      acc_fill++;
    }
    acc_total += take;
    samples -= take;
  }
}

// ------------------------------------------------------------------------------------------ microphone
static bool mic_init() {
  i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
  chan_cfg.dma_desc_num = 12;                        // 12 x 256 frames = 192 ms of buffering
  chan_cfg.dma_frame_num = 256;
  if (i2s_new_channel(&chan_cfg, nullptr, &rx_chan) != ESP_OK) return false;
  i2s_std_config_t std_cfg = {
      .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(hw::MIC_FS),
      .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
      .gpio_cfg = {
          .mclk = I2S_GPIO_UNUSED,
          .bclk = (gpio_num_t)PIN_I2S_SCK,
          .ws = (gpio_num_t)PIN_I2S_WS,
          .dout = I2S_GPIO_UNUSED,
          .din = (gpio_num_t)PIN_I2S_SD,
          .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false},
      },
  };
  std_cfg.slot_cfg.slot_mask = MIC_USE_RIGHT_SLOT ? I2S_STD_SLOT_RIGHT : I2S_STD_SLOT_LEFT;
  if (i2s_channel_init_std_mode(rx_chan, &std_cfg) != ESP_OK) return false;
  return i2s_channel_enable(rx_chan) == ESP_OK;
}

// ------------------------------------------------------------------------------------------- sensing
static void dump_row(uint32_t now, const char* ch, const float* f) {
  Serial.printf("F,%lu,%c,%s", (unsigned long)now, dump_label, ch);
  for (int i = 0; i < hw::NFEAT; i++) Serial.printf(",%.4f", f[i]);
  Serial.println();
}

static void sense_task(void*) {
  uint32_t last_hb = millis();
  int epochs = 0;
  for (;;) {
    if (mpu_probe_req) { mpu_probe(); mpu_probe_req = false; }
    const uint32_t start = millis();
    int mic_fill = 0;
    bool mic_ok = mic_present;
    if (mic_present) {
      while (mic_fill < hw::MIC_N) {
        size_t got = 0;
        const esp_err_t e = i2s_channel_read(rx_chan, i2sraw, sizeof(i2sraw), &got, 100);
        if (e == ESP_OK && got >= 4) {
          const int n = (int)(got / 4);
          for (int i = 0; i < n && mic_fill < hw::MIC_N; i++)
            micbuf[mic_fill++] = (float)(i2sraw[i] >> 8) * (1.0f / 8388608.0f);   // 24-bit sample in the top of a 32-bit slot
        } else {
          mic_errs++;
          if (millis() - start > EPOCH_DEADLINE_MS) { mic_ok = false; break; }
        }
        drain_acc();                                // keep the accelerometer FIFO from filling while we wait on audio
      }
    } else {
      while (millis() - start < 512) { drain_acc(); vTaskDelay(pdMS_TO_TICKS(20)); }
    }
    while (mpu_present && acc_fill < hw::ACC_N && millis() - start < EPOCH_DEADLINE_MS) { vTaskDelay(pdMS_TO_TICKS(3)); drain_acc(); }

    const bool acc_ok = mpu_present && acc_fill >= hw::ACC_N;
    hw::BlockStats ca, cm;
    const bool warm = epochs++ < WARMUP_EPOCHS;
    if (acc_ok && !warm) hw::acc_block(accb[0], accb[1], accb[2], MAINS_HZ, logic.prev_acc(), &ca, sre, sim_);
    if (mic_ok && !warm) hw::mic_block(micbuf, MAINS_HZ, logic.prev_mic(), &cm, micbuf, imbuf);   // in place: x and re are the same buffer

    // hand the accelerometer samples beyond one block to the next epoch; if we are badly behind, resynchronize
    if (acc_fill >= hw::ACC_N) {
      const int extra = acc_fill - hw::ACC_N;
      if (extra > hw::ACC_N / 2) { acc_fill = 0; }
      else {
        for (int a = 0; a < 3; a++) memmove(accb[a], accb[a] + hw::ACC_N, (size_t)extra * sizeof(float));
        acc_fill = extra;
      }
    } else if (mpu_present) {
      mpu_fifo_reset();                             // a short block means samples were lost: discard and resync
    }

    const uint32_t now = millis();
    hw::EpochIn in = {now, acc_ok && !warm ? &ca : nullptr, mic_ok && !warm ? &cm : nullptr};
    lock();
    const hw::EpochOut out = logic.epoch(in);
    const hw::Phase ph = logic.phase();
    unlock();
    epochs_done++;
    lock(); const bool no_mem = logic.take_cal_nomem(); unlock();
    if (no_mem) Serial.printf("W,calibration could not start: not enough memory (free %u bytes, largest piece %u). Restart the node and try again\n",
                              (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMaxAllocHeap());

    if (dump_on) {
      const float* fa = logic.last_acc_features();
      const float* fm = logic.last_mic_features();
      if (fa) dump_row(now, "a", fa);
      if (fm) dump_row(now, "m", fm);
    }
    if (ph == hw::PH_RUN && out.dec.event) {
      Serial.printf("I,pump %c conf %d (acc %c, mic %c)\n", out.dec.st, out.dec.c, out.dec.ca, out.dec.cm);
      queue_message("ev");
    }
    if (now - last_hb >= (uint32_t)HEARTBEAT_S * 1000) { last_hb = now; queue_message("hb"); }
    lock(); const bool dirty = logic.take_dirty(); unlock();
    if (dirty) {
      const hw::CalResult a = logic.last_cal_acc(), m = logic.last_cal_mic();
      Serial.printf("I,calibration: accelerometer %s (%.2f), microphone %s (%.2f)\n",
                    a.verdict == hw::GOOD ? "GOOD" : a.verdict == hw::FAIR ? "FAIR" : "POOR", a.quality,
                    m.verdict == hw::GOOD ? "GOOD" : m.verdict == hw::FAIR ? "FAIR" : "POOR", m.quality);
      save_models();
    }
  }
}

// -------------------------------------------------------------------------------------- serial commands
static void print_status() {
  const float secs = (millis() - acc_started_ms) / 1000.0f;
  const float rate = secs > 5 ? acc_total / secs : 0;
  lock();
  const hw::Decision d = logic.decision();
  const int phase = (int)logic.phase();
  const int va = (int)logic.verdict_acc(), vm = (int)logic.verdict_mic();
  const bool off = logic.offline(millis());
  unlock();
  Serial.printf("S,phase=%d,state=%c,conf=%d,acc=%c,mic=%c,verdict_acc=%d,verdict_mic=%d,offline=%d,mains=%d,", phase, d.st, d.c,
                d.ca, d.cm, va, vm, off ? 1 : 0, logic.mains_ok() ? 1 : 0);
  Serial.printf("acc_rate=%.1f,acc_overflows=%lu,i2c_errs=%lu,mic_errs=%lu,epochs=%lu,sent=%lu,dropped=%lu,rejected=%lu,heap=%u,rssi=%d,time=%d,heap_max=%u,acc_cfg=%d,acc_who=0x%02x\n",
                rate, (unsigned long)acc_overflows, (unsigned long)acc_i2c_errs, (unsigned long)mic_errs, (unsigned long)epochs_done,
                (unsigned long)msgs_sent, (unsigned long)msgs_dropped, (unsigned long)msgs_rejected, (unsigned)ESP.getFreeHeap(),
                WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0, epoch_ms() ? 1 : 0, (unsigned)ESP.getMaxAllocHeap(), mpu_cfg_idx, mpu_who);
  if (rate > 0 && (rate < 990 || rate > 1010))
    Serial.printf("W,accelerometer sample rate is %.1f Hz, not 1000: the mains hum bands may sit slightly off (see README)\n", rate);
}

static void handle_command(char* line) {
  if (!strncmp(line, "label ", 6)) {
    const char c = line[6];
    dump_label = (c == 'R' || c == 'S' || c == '-') ? c : '-';
    Serial.printf("I,label %c\n", dump_label);
  } else if (!strcmp(line, "dump on")) { dump_on = true; Serial.println("I,feature dump on: rows F,ms,label,channel,f0..f14");
  } else if (!strcmp(line, "dump off")) { dump_on = false; Serial.println("I,feature dump off");
  } else if (!strcmp(line, "status")) { print_status();
  } else if (!strcmp(line, "mpu")) { mpu_probe_req = true; Serial.println("I,mpu probe requested, results follow within a second");
  } else if (!strcmp(line, "wipe")) { prefs.remove("model"); Serial.println("I,model erased, restarting"); delay(200); ESP.restart();
  } else {
    Serial.println("I,commands: status | mpu | dump on | dump off | label R | label S | label - | wipe");
  }
}

// ------------------------------------------------------------------------------------------ setup / loop
void setup() {
  Serial.begin(115200);
  delay(300);
  load_secret();

  const String md5 = ESP.getSketchMD5();
  strncpy(fw_hash, md5.c_str(), 8);
  fw_hash[8] = 0;

  micbuf = (float*)malloc(sizeof(float) * hw::MIC_N);
  imbuf = (float*)malloc(sizeof(float) * hw::MIC_N);
  if (!micbuf || !imbuf) fatal("not enough memory for the audio buffers");

  prefs.begin(NVS_NS, false);
  boot_count = prefs.getUInt("boot", 0) + 1;         // the server rejects any (boot, seq) it has already seen
  prefs.putUInt("boot", boot_count);

  mtx = xSemaphoreCreateMutex();
  txq = xQueueCreate(8, sizeof(OutMsg));
  if (!mtx || !txq) fatal("could not create the task primitives");

  hw::Stored st;
  const bool have = load_models(st);
  logic.begin(have ? &st : nullptr, (uint32_t)HEARTBEAT_S * 1000, millis());
  Serial.printf("I,Witness Node %s boot %lu firmware %s mains %d Hz, %s\n", NODE_ID, (unsigned long)boot_count, fw_hash, MAINS_HZ,
                have ? "calibration found" : "not calibrated: hold the button 3 s with the pump OFF");

  mpu_present = mpu_init(true);
  if (!mpu_present) Serial.println("W,accelerometer not usable: running on the microphone alone");
  mic_present = mic_init();
  if (!mic_present) Serial.println("W,microphone could not start: running on the accelerometer alone");
  if (!mpu_present && !mic_present) fatal("neither sensor works: check the wiring (docs/WIRING.md)");

  xTaskCreatePinnedToCore(ui_task, "ui", 4096, nullptr, 2, nullptr, 0);
  xTaskCreatePinnedToCore(net_task, "net", USE_TLS ? 12288 : 8192, nullptr, 1, nullptr, 0);
  xTaskCreatePinnedToCore(sense_task, "sense", 8192, nullptr, 3, nullptr, 1);
}

void loop() {
  static char line[48];
  static int n = 0;
  static uint32_t last_status = 0;
  while (Serial.available()) {
    const char c = (char)Serial.read();
    if (c == '\n' || c == '\r') { line[n] = 0; if (n) handle_command(line); n = 0; }
    else if (n < (int)sizeof(line) - 1) line[n++] = c;
  }
  if (millis() - last_status > 60000) { last_status = millis(); print_status(); }
  delay(20);
}
