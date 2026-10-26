// Host-side driver for the firmware's application logic (hw_node.h). Built and run by tests/test_firmware_node.py.
//
//   node_cli <mains_hz> <acc.f32> <mic.f32> <script>
//
// acc.f32  planar float32: all x samples, then all y, then all z (1000 Hz)
// mic.f32  float32 samples (16000 Hz)
// script   one command per line:  <t_ms> down | up | trust g|a|r | link up|down
//
// It does what witness_node.ino does, in the same order, with the hardware replaced by files: every 512 ms it
// cuts one block per channel, runs the DSP, hands the blocks to NodeLogic, polls the button every 10 ms, and
// every 10 s counts as a heartbeat that reaches the server if the link is up.
//
// Output lines:
//   E now phase st conf ca cm event acc_win mic_win za zm   one per epoch
//   O now g a r buz                                          whenever the LED or buzzer pattern changes
//   P now phase                                              whenever the phase changes
//   D now va vm acc_quality mic_quality acc_mu0 mic_mu0      whenever the stored models changed
//   M now                                                    a calibration could not start: not enough memory
//   X phase usable_acc usable_mic                            after rebuilding from the exported flash image
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include <vector>

// HW_MAX_ALLOC (environment, bytes): the largest single block the pretend heap will give. The first real board had 68 kB
// free in total but no piece of 31 kB, so the calibration memory could not be had in one block.
static size_t g_max_alloc = (size_t)-1;
static void* limited_malloc(size_t n) { return n > g_max_alloc ? nullptr : malloc(n); }
#define HW_CAL_MALLOC limited_malloc

#include "../witness_node/hw_dsp.h"
#include "../witness_node/hw_node.h"

struct Cmd { uint32_t t; std::string what, arg; };

static std::vector<float> load(const char* path) {
  std::vector<float> v;
  FILE* f = fopen(path, "rb");
  if (!f) { fprintf(stderr, "cannot open %s\n", path); exit(2); }
  fseek(f, 0, SEEK_END);
  const long n = ftell(f);
  fseek(f, 0, SEEK_SET);
  v.resize(n / sizeof(float));
  if (fread(v.data(), sizeof(float), v.size(), f) != v.size()) exit(2);
  fclose(f);
  return v;
}

int main(int argc, char** argv) {
  if (argc != 5) { fprintf(stderr, "usage: node_cli mains acc.f32 mic.f32 script\n"); return 2; }
  const int mains = atoi(argv[1]);
  std::vector<float> acc = load(argv[2]), mic = load(argv[3]);
  const size_t acc_n = acc.size() / 3;
  std::vector<Cmd> cmds;
  {
    FILE* f = fopen(argv[4], "r");
    if (!f) return 2;
    char a[32], b[32];
    unsigned long t;
    while (fscanf(f, "%lu %31s", &t, a) == 2) {
      b[0] = 0;
      if (!strcmp(a, "trust") || !strcmp(a, "link")) { if (fscanf(f, "%31s", b) != 1) return 2; }
      cmds.push_back({(uint32_t)t, a, b});
    }
    fclose(f);
  }

  if (const char* cap = getenv("HW_MAX_ALLOC")) g_max_alloc = (size_t)atol(cap);
  const uint32_t HB = 10000;
  hw::NodeLogic logic;
  logic.begin(nullptr, HB, 0);

  std::vector<float> re(hw::MIC_N), im(hw::MIC_N);
  std::vector<float> micbuf(hw::MIC_N);
  hw::BlockStats cur_a, cur_m;

  bool down = false, link = true;
  char trust = 0;
  size_t ci = 0;
  hw::Phase last_phase = logic.phase();
  hw::Outputs last_out = {false, false, false, false};
  bool first_out = true;
  uint32_t next_hb = HB;

  const uint32_t EPOCH_MS = 512;
  for (size_t k = 0;; k++) {
    if ((k + 1) * hw::ACC_N > acc_n || (k + 1) * hw::MIC_N > mic.size()) break;
    const uint32_t t0 = (uint32_t)(k * EPOCH_MS);
    for (uint32_t t = t0; t < t0 + EPOCH_MS; t += 10) {
      while (ci < cmds.size() && cmds[ci].t <= t) {
        const Cmd& c = cmds[ci++];
        if (c.what == "down") down = true;
        else if (c.what == "up") down = false;
        else if (c.what == "trust") trust = c.arg[0];
        else if (c.what == "link") link = c.arg == "up";
      }
      logic.button(down, t);
      if (t >= next_hb) { next_hb += HB; if (link) logic.note_post(true, trust, t); }
      const hw::Outputs o = logic.outputs(t);
      if (first_out || o.g != last_out.g || o.a != last_out.a || o.r != last_out.r || o.buz != last_out.buz) {
        printf("O %u %d %d %d %d\n", t, o.g, o.a, o.r, o.buz);
        last_out = o; first_out = false;
      }
      if (logic.phase() != last_phase) { last_phase = logic.phase(); printf("P %u %d\n", t, (int)last_phase); }
    }
    const uint32_t now = t0 + EPOCH_MS;

    const float* ax = &acc[k * hw::ACC_N];
    const float* ay = &acc[acc_n + k * hw::ACC_N];
    const float* az = &acc[2 * acc_n + k * hw::ACC_N];
    hw::acc_block(ax, ay, az, mains, logic.prev_acc(), &cur_a, re.data(), im.data());
    memcpy(micbuf.data(), &mic[k * hw::MIC_N], sizeof(float) * hw::MIC_N);
    hw::mic_block(micbuf.data(), mains, logic.prev_mic(), &cur_m, re.data(), im.data());

    hw::EpochIn in = {now, &cur_a, &cur_m};
    const hw::EpochOut out = logic.epoch(in);
    printf("E %u %d %c %d %c %c %d %d %d %.3f %.3f\n", now, (int)logic.phase(), out.dec.st, out.dec.c, out.dec.ca,
           out.dec.cm, out.dec.event ? 1 : 0, out.acc_window ? 1 : 0, out.mic_window ? 1 : 0, out.za, out.zm);
    if (logic.phase() != last_phase) { last_phase = logic.phase(); printf("P %u %d\n", now, (int)last_phase); }
    if (logic.take_cal_nomem()) printf("M %u\n", now);
    if (logic.take_dirty()) {
      printf("D %u %d %d %.4f %.4f %.4f %.4f\n", now, (int)logic.verdict_acc(), (int)logic.verdict_mic(),
             logic.last_cal_acc().quality, logic.last_cal_mic().quality, logic.acc_model().mu_r[0],
             logic.mic_model().mu_r[0]);
    }
  }

  hw::Stored st;
  logic.export_stored(st);
  hw::NodeLogic again;
  again.begin(&st, HB, 0);
  printf("X %d %d %d\n", (int)again.phase(), again.usable_acc() ? 1 : 0, again.usable_mic() ? 1 : 0);
  return 0;
}
