// Witness Node application logic: install calibration, button, LEDs, buzzer, drift confirmation.
// Portable on purpose (no Arduino or ESP-IDF headers) so tests/test_firmware_node.py can run the whole
// behaviour on a normal computer against synthetic signals. witness_node.ino only moves bytes between the
// hardware and this class.
//
// Time: every function takes `now` in milliseconds from a monotonic clock (millis()). All comparisons use
// unsigned subtraction, so the 49-day wrap of millis() is harmless.
#pragma once
#include <stdlib.h>
#include <string.h>

#include "hw_model.h"
#include "hw_wire.h"

#ifndef HW_CAL_MALLOC
#define HW_CAL_MALLOC malloc            // the host test replaces this with an allocator that refuses big blocks
#endif

namespace hw {

constexpr uint32_t CAL_SECONDS = 120;          // length of each baseline recording
constexpr uint32_t CAL_SKIP_MS = 5000;         // ignored after each start: the operator's hand is on the box
constexpr uint32_t LONG_PRESS_MS = 3000;       // hold this long to start or cancel calibration
constexpr uint32_t DEBOUNCE_MS = 30;
constexpr uint32_t MUTE_MS = 600000UL;         // a short press silences the alarm for ten minutes
constexpr uint32_t WAIT_ON_TIMEOUT_MS = 600000UL;
constexpr int CAL_MAX_WINDOWS = 128;           // 120 s of non-overlapping 1.024 s windows is 117
constexpr int CAL_MIN_WINDOWS = 40;            // fewer than this and the channel is not trusted
constexpr int CAL_SMALLEST_CAP = 48;           // if memory is short, a calibration keeps this many windows per baseline at least (must be >= CAL_MIN_WINDOWS)
constexpr int CONFIRM_MIN_CONF = 850;          // only a state both channels agree on can be confirmed
constexpr float ADAPT_ALPHA = 0.02f;
constexpr float ACC_DEAD_VAR = 1e-10f;         // g^2: a stuck or unplugged accelerometer
constexpr float MIC_DEAD_VAR = 1e-12f;         // full-scale^2: digital silence, an unplugged microphone

enum Phase : uint8_t { PH_UNCAL, PH_RUN, PH_CAL_OFF, PH_CAL_WAIT_ON, PH_CAL_ON };
enum Press : uint8_t { PRESS_NONE, PRESS_SHORT, PRESS_LONG };
enum Overlay : uint8_t { OVL_NONE, OVL_CAL_GOOD, OVL_CAL_FAIR, OVL_CAL_POOR, OVL_CONFIRMED, OVL_REFUSED };

struct Stored {                                // what is kept in flash between power cycles
  Model acc, mic;
  uint8_t va, vm;                              // Verdict of each channel at the last calibration
};

struct Outputs { bool g, a, r, buz; };

struct EpochIn {
  uint32_t now;
  const BlockStats* acc;                       // nullptr when no good accelerometer block was captured
  const BlockStats* mic;                       // nullptr when no good microphone block was captured
};

struct EpochOut {
  Decision dec;                                // reported state; dec.event is true when it just changed
  bool acc_window, mic_window;                 // a feature window was formed this epoch
  float za, zm;
};

class NodeLogic {
 public:
  void begin(const Stored* st, uint32_t hb_ms, uint32_t now) {
    hb_ms_ = hb_ms;
    last_ok_ms_ = now;
    memset(&acc_, 0, sizeof(acc_));
    memset(&mic_, 0, sizeof(mic_));
    va_ = vm_ = POOR;
    if (st) { acc_ = st->acc; mic_ = st->mic; va_ = (Verdict)st->va; vm_ = (Verdict)st->vm; }
    refresh_usable();
    set_phase(usable_acc_ || usable_mic_ ? PH_RUN : PH_UNCAL, now);
  }

  // ------------------------------------------------------------------------------- sampling
  const BlockStats* prev_acc() const { return prev_acc_ok_ ? &prev_acc_ : nullptr; }
  const BlockStats* prev_mic() const { return prev_mic_ok_ ? &prev_mic_ : nullptr; }

  static bool acc_dead(const BlockStats& b) { return b.var[0] + b.var[1] + b.var[2] < ACC_DEAD_VAR; }
  static bool mic_dead(const BlockStats& b) { return b.var[0] < MIC_DEAD_VAR; }

  EpochOut epoch(const EpochIn& in) {
    EpochOut out;
    memset(&out, 0, sizeof(out));
    out.dec = idle();
    epoch_idx_++;
    const BlockStats* a = in.acc && !acc_dead(*in.acc) ? in.acc : nullptr;
    const BlockStats* m = in.mic && !mic_dead(*in.mic) ? in.mic : nullptr;
    wa_ = wm_ = false;
    if (a) {
      if (prev_acc_ok_) { window_features(true, prev_acc_, *a, fa_); wa_ = true; }
      prev_acc_ = *a; prev_acc_ok_ = true;
    } else prev_acc_ok_ = false;
    if (m) {
      if (prev_mic_ok_) { window_features(false, prev_mic_, *m, fm_); wm_ = true; }
      prev_mic_ = *m; prev_mic_ok_ = true;
    } else prev_mic_ok_ = false;
    out.acc_window = wa_;
    out.mic_window = wm_;

    switch (phase_) {
      case PH_RUN: {
        char ra = 'U', rm = 'U';
        float za = 0, zm = 0;
        if (usable_acc_ && wa_) { za = acc_.z(fa_); ra = z_to_state(za, true); }
        if (usable_mic_ && wm_) { zm = mic_.z(fm_); rm = z_to_state(zm, true); }
        out.za = za; out.zm = zm;
        dec_ = decider_.step(ra, za, rm, zm);
        out.dec = dec_;
        if (dec_.event) beep(1, in.now);
        break;
      }
      case PH_CAL_OFF:
      case PH_CAL_ON: record(in.now); break;
      case PH_CAL_WAIT_ON:
        if (in.now - phase_ms_ > WAIT_ON_TIMEOUT_MS) cancel_cal(in.now);   // nobody came back
        break;
      case PH_UNCAL: break;
    }
    return out;
  }

  // ---------------------------------------------------------------------------------- button
  // Call every few milliseconds with the raw switch level (true = pressed).
  void button(bool down, uint32_t now) {
    Press p = PRESS_NONE;
    if (down != b_raw_) { b_raw_ = down; b_raw_ms_ = now; }
    if (b_stable_ != b_raw_ && now - b_raw_ms_ >= DEBOUNCE_MS) {
      b_stable_ = b_raw_;
      if (b_stable_) { b_down_ms_ = now; b_long_ = false; }
      else if (!b_long_) p = PRESS_SHORT;
    }
    if (b_stable_ && !b_long_ && now - b_down_ms_ >= LONG_PRESS_MS) { b_long_ = true; p = PRESS_LONG; }
    if (p != PRESS_NONE) on_press(p, now);
  }

  // ----------------------------------------------------------------------------- server link
  // Call after each POST. trust is 'g', 'a' or 'r' from the server's answer, or 0 when unknown.
  void note_post(bool accepted, char trust, uint32_t now) {
    if (!accepted) return;
    last_ok_ms_ = now;
    if (trust == 'g' || trust == 'a' || trust == 'r') {
      trust_ = trust;
      trust_ms_ = now;
      if (trust != 'r') muted_ = false;           // a later red alert must sound again
    }
  }
  bool offline(uint32_t now) const { return now - last_ok_ms_ > 3 * hb_ms_; }

  void mains(int rail_mv, uint32_t now, int lost_below_mv, uint32_t hold_ms) {   // rail_mv: wall-side 5 V rail
    if (rail_mv < lost_below_mv) {
      if (!mains_low_) { mains_low_ = true; mains_low_ms_ = now; }
      if (now - mains_low_ms_ >= hold_ms) mains_ok_ = false;
    } else { mains_low_ = false; mains_ok_ = true; }
  }
  bool mains_ok() const { return mains_ok_; }

  // ---------------------------------------------------------------------------- message fields
  void fill(Wire& w) const {
    const Decision d = phase_ == PH_RUN ? dec_ : idle();
    w.st = d.st; w.c = d.c; w.ca = d.ca; w.cm = d.cm;
    w.p = mains_ok_ ? 1 : 0;
  }

  // -------------------------------------------------------------------------------- outputs
  Outputs outputs(uint32_t now) const {
    Outputs o = {false, false, false, false};
    if (ovl_ != OVL_NONE && now - ovl_ms_ < overlay_len(ovl_)) { overlay(ovl_, now - ovl_ms_, o); return o; }
    const uint32_t t = now - phase_ms_;
    const bool calibrating = phase_ == PH_CAL_OFF || phase_ == PH_CAL_WAIT_ON || phase_ == PH_CAL_ON;
    switch (phase_) {
      case PH_CAL_OFF: o.r = (t % 250) < 125; break;                  // recording the STOPPED baseline
      case PH_CAL_WAIT_ON: o.g = (t % 1000) < 500; break;             // start the pump, then press
      case PH_CAL_ON: o.g = (t % 250) < 125; break;                   // recording the RUNNING baseline
      case PH_UNCAL: o.a = (t % 2000) < 1000; break;                  // needs calibration
      case PH_RUN: {
        const char s = dec_.st;
        const bool off = offline(now);
        o.g = s == 'R';
        o.r = s == 'S';
        o.a = s != 'R' && s != 'S';
        if (off) {                                                    // lost the server: add a 2 Hz amber blink
          if (s == 'R' || s == 'S') o.a = (now % 2000) < 100;
          else o.a = (now % 500) < 250;
        }
        break;
      }
    }
    if (beep_n_ && now - beep_ms_ < (uint32_t)beep_n_ * 300) o.buz = ((now - beep_ms_) % 300) < 150;
    if (!calibrating && alarm_active(now)) {
      const uint32_t p = now % 15000;
      o.buz = o.buz || p < 150 || (p >= 300 && p < 450) || (p >= 600 && p < 750);
    }
    return o;
  }

  // ------------------------------------------------------------------------------- accessors
  Phase phase() const { return phase_; }
  const Decision& decision() const { return dec_; }
  bool usable_acc() const { return usable_acc_; }
  bool usable_mic() const { return usable_mic_; }
  Verdict verdict_acc() const { return va_; }
  Verdict verdict_mic() const { return vm_; }
  const Model& acc_model() const { return acc_; }
  const Model& mic_model() const { return mic_; }
  const float* last_acc_features() const { return wa_ ? fa_ : nullptr; }
  const float* last_mic_features() const { return wm_ ? fm_ : nullptr; }
  int cal_windows(int slot) const { return cal_n_[slot]; }       // 0 acc R, 1 acc S, 2 mic R, 3 mic S
  bool alarm_active(uint32_t now) const {
    return trust_ == 'r' && now - trust_ms_ <= 3 * hb_ms_ && !(muted_ && now - muted_ms_ < MUTE_MS);
  }
  Overlay overlay_kind() const { return ovl_; }

  // Set when the stored models changed (calibration finished or a confirmation adapted them).
  bool take_dirty() { const bool d = dirty_; dirty_ = false; return d; }
  // True once after a calibration could not start for lack of memory, so the sketch can print it.
  bool take_cal_nomem() { const bool n = cal_nomem_; cal_nomem_ = false; return n; }
  // 'K' after a calibration started, 'X' after one was cancelled, else 0; cleared by reading, so the sketch can log a button press.
  int cal_capacity() const { return cal_cap_; }     // windows per baseline the running calibration can keep (0 when none runs)
  char take_button_event() { const char e = ev_; ev_ = 0; return e; }
  void export_stored(Stored& s) const { s.acc = acc_; s.mic = mic_; s.va = (uint8_t)va_; s.vm = (uint8_t)vm_; }

  // Result of the most recent calibration, for the serial log.
  CalResult last_cal_acc() const { return cal_acc_; }
  CalResult last_cal_mic() const { return cal_mic_; }

  ~NodeLogic() { free_cal(); }

 private:
  Phase phase_ = PH_UNCAL;
  uint32_t phase_ms_ = 0, hb_ms_ = 10000, last_ok_ms_ = 0, trust_ms_ = 0;
  char trust_ = 0;

  Model acc_, mic_;
  Verdict va_ = POOR, vm_ = POOR;
  bool usable_acc_ = false, usable_mic_ = false;
  Decider decider_;
  Decision dec_ = {'U', 200, 'U', 'U', false};

  BlockStats prev_acc_, prev_mic_;
  bool prev_acc_ok_ = false, prev_mic_ok_ = false;
  float fa_[NFEAT], fm_[NFEAT];
  bool wa_ = false, wm_ = false;
  uint32_t epoch_idx_ = 0;

  uint16_t* cal_[4] = {nullptr, nullptr, nullptr, nullptr};   // 4 separate blocks of CAL_MAX_WINDOWS x NFEAT half-precision numbers, only while calibrating
  int cal_cap_ = 0;                               // windows per baseline the current calibration has room for
  bool cal_nomem_ = false;                        // the last attempt to start a calibration found too little memory
  char ev_ = 0;                                   // last button event for the serial log: 'K' calibration started, 'X' cancelled
  int cal_n_[4] = {0, 0, 0, 0};
  uint32_t cal_start_ = 0;
  CalResult cal_acc_ = {0, 0, 0, POOR}, cal_mic_ = {0, 0, 0, POOR};

  bool b_raw_ = false, b_stable_ = false, b_long_ = false;
  uint32_t b_raw_ms_ = 0, b_down_ms_ = 0;

  Overlay ovl_ = OVL_NONE;
  uint32_t ovl_ms_ = 0, beep_ms_ = 0;
  int beep_n_ = 0;
  bool muted_ = false, mains_ok_ = true, mains_low_ = false, dirty_ = false;
  uint32_t muted_ms_ = 0, mains_low_ms_ = 0;

  static Decision idle() { return Decision{'U', 200, 'U', 'U', false}; }

  void set_phase(Phase p, uint32_t now) { phase_ = p; phase_ms_ = now; }
  void beep(int n, uint32_t now) { beep_n_ = n; beep_ms_ = now; }
  void show(Overlay k, uint32_t now) { ovl_ = k; ovl_ms_ = now; }
  void refresh_usable() {
    usable_acc_ = acc_.valid() && va_ != POOR;
    usable_mic_ = mic_.valid() && vm_ != POOR;
  }

  uint16_t* slot(int i) { return cal_[i]; }
  void free_cal() { for (int i = 0; i < 4; i++) { if (cal_[i]) free(cal_[i]); cal_[i] = nullptr; } }
  // Four blocks of 3.8 kB (15 kB in all), taken from the heap only while a calibration runs. They used to be one block of 31 kB, which
  // never fitted: the memory the Arduino-ESP32 core reports as free also counts instruction RAM that malloc cannot use, so on the
  // first real board "67 kB free, largest piece 36 kB" was really about 31 kB of usable memory shared by Wi-Fi and the calibration.
  // If even that is not there (Wi-Fi busy at that moment), try room for fewer windows: 128, then 96, 64 and 48 per baseline. Fewer windows
  // only make the calibration's own accuracy test a little noisier; it never needs fewer than CAL_MIN_WINDOWS.
  bool alloc_cal() {
    free_cal();
    static const int caps[] = {CAL_MAX_WINDOWS, 96, 64, CAL_SMALLEST_CAP};
    for (int c : caps) {
      bool ok = true;
      for (int i = 0; i < 4 && ok; i++) {
        cal_[i] = (uint16_t*)HW_CAL_MALLOC(sizeof(uint16_t) * (size_t)c * NFEAT);
        ok = cal_[i] != nullptr;
      }
      if (ok) { cal_cap_ = c; return true; }
      free_cal();
    }
    cal_cap_ = 0;
    return false;
  }

  void on_press(Press p, uint32_t now) {
    if (p == PRESS_LONG) {
      if (phase_ == PH_RUN || phase_ == PH_UNCAL) start_cal(now);
      else cancel_cal(now);
      return;
    }
    switch (phase_) {
      case PH_CAL_WAIT_ON: begin_on(now); break;
      case PH_RUN:
      case PH_UNCAL:
        if (alarm_active(now)) { muted_ = true; muted_ms_ = now; }    // silencing the alarm never trains the node
        else if (phase_ == PH_RUN) confirm(now);
        break;
      default: break;
    }
  }

  void start_cal(uint32_t now) {
    if (!alloc_cal()) { cal_nomem_ = true; show(OVL_CAL_POOR, now); beep(3, now); return; }    // no memory: say so, change nothing
    cal_nomem_ = false;
    ev_ = 'K';
    for (int i = 0; i < 4; i++) cal_n_[i] = 0;
    cal_start_ = now;
    ovl_ = OVL_NONE;
    set_phase(PH_CAL_OFF, now);
    beep(1, now);
  }

  void cancel_cal(uint32_t now) {
    ev_ = 'X';
    free_cal();
    set_phase(usable_acc_ || usable_mic_ ? PH_RUN : PH_UNCAL, now);
    decider_.reset();
    dec_ = idle();
    beep(1, now);
  }

  void begin_on(uint32_t now) {
    cal_start_ = now;
    set_phase(PH_CAL_ON, now);
    beep(1, now);
  }

  static void store_row(uint16_t* dst, const float* src) { for (int i = 0; i < NFEAT; i++) dst[i] = float_to_half(src[i]); }

  void record(uint32_t now) {
    const uint32_t el = now - cal_start_;
    const bool off = phase_ == PH_CAL_OFF;
    if (el >= CAL_SKIP_MS && (epoch_idx_ & 1u) == 0) {
      if (wa_) { const int s = off ? 1 : 0; if (cal_n_[s] < cal_cap_) store_row(slot(s) + cal_n_[s]++ * NFEAT, fa_); }
      if (wm_) { const int s = off ? 3 : 2; if (cal_n_[s] < cal_cap_) store_row(slot(s) + cal_n_[s]++ * NFEAT, fm_); }
    }
    if (el >= CAL_SKIP_MS + CAL_SECONDS * 1000UL) {
      if (off) { set_phase(PH_CAL_WAIT_ON, now); beep(2, now); }
      else finish_cal(now);
    }
  }

  void finish_cal(uint32_t now) {
    Model ma, mm;
    memset(&ma, 0, sizeof(ma));
    memset(&mm, 0, sizeof(mm));
    cal_acc_ = {0, 0, 0, POOR};
    cal_mic_ = {0, 0, 0, POOR};
    if (cal_n_[0] >= CAL_MIN_WINDOWS && cal_n_[1] >= CAL_MIN_WINDOWS) cal_acc_ = calibrate_rows(HalfRows{slot(0)}, cal_n_[0], HalfRows{slot(1)}, cal_n_[1], ma);
    if (cal_n_[2] >= CAL_MIN_WINDOWS && cal_n_[3] >= CAL_MIN_WINDOWS) cal_mic_ = calibrate_rows(HalfRows{slot(2)}, cal_n_[2], HalfRows{slot(3)}, cal_n_[3], mm);
    acc_ = ma; mic_ = mm;
    va_ = cal_acc_.verdict; vm_ = cal_mic_.verdict;
    refresh_usable();
    free_cal();
    decider_.reset();
    dec_ = idle();
    dirty_ = true;
    const bool any = usable_acc_ || usable_mic_;
    const bool both_good = usable_acc_ && usable_mic_ && va_ == GOOD && vm_ == GOOD;
    set_phase(any ? PH_RUN : PH_UNCAL, now);
    show(!any ? OVL_CAL_POOR : both_good ? OVL_CAL_GOOD : OVL_CAL_FAIR, now);
    beep(0, now);                                                    // the overlay carries its own beeps
  }

  void confirm(uint32_t now) {
    const bool strong = (dec_.st == 'R' || dec_.st == 'S') && dec_.c >= CONFIRM_MIN_CONF;
    const bool have = (!usable_acc_ || wa_) && (!usable_mic_ || wm_);
    if (!strong || !have) { show(OVL_REFUSED, now); return; }
    const bool running = dec_.st == 'R';
    if (usable_acc_) adapt(acc_, fa_, running, ADAPT_ALPHA);
    if (usable_mic_) adapt(mic_, fm_, running, ADAPT_ALPHA);
    dirty_ = true;
    show(OVL_CONFIRMED, now);
  }

  static uint32_t overlay_len(Overlay k) {
    switch (k) {
      case OVL_CAL_GOOD: case OVL_CAL_FAIR: return 1500;
      case OVL_CAL_POOR: return 10000;
      case OVL_CONFIRMED: return 400;
      case OVL_REFUSED: return 800;
      default: return 0;
    }
  }

  // Every flash starts after a short dark gap, so it is never mistaken for the blinking that came before it.
  static bool flash(uint32_t t, uint32_t n) { return t < n * 400 && (t % 400) >= 200; }

  static void overlay(Overlay k, uint32_t t, Outputs& o) {
    switch (k) {
      case OVL_CAL_GOOD: o.g = flash(t, 3); o.buz = t < 600; break;                 // three green flashes, one long beep
      case OVL_CAL_FAIR: o.a = flash(t, 3); o.buz = t < 150 || (t >= 300 && t < 450); break;
      case OVL_CAL_POOR: {
        if (t < 1400) o.r = flash(t, 3);
        else { const bool alt = ((t - 1400) / 500) & 1u; o.r = !alt; o.a = alt; }   // "move me": red/amber alternate
        o.buz = t < 150 || (t >= 300 && t < 450) || (t >= 600 && t < 750);
        break;
      }
      case OVL_CONFIRMED: o.g = o.a = o.r = t < 250; o.buz = t < 60; break;
      case OVL_REFUSED: o.a = flash(t, 2); break;                                    // two amber blinks: "not now"
      default: break;
    }
  }
};

}  // namespace hw
