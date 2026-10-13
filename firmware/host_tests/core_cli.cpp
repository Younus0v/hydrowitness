// Host-side driver for the firmware's portable core. Built and run by tests/test_firmware_core.py.
//   core_cli hmac <key-hex> <message>            prints the HMAC-SHA256 hex digest
//   core_cli wire <key-hex> n b s t_ms k st c ca cm p f   prints the signed JSON body
//   core_cli feat <acc|mic> <mains>               reads blocks from stdin, prints window features
//   core_cli calib                                reads labeled feature rows (R/S/Q), prints the model
//   core_cli decide                               reads "za zm usable_a usable_m" rows, prints decisions
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vector>

#include "../witness_node/hw_dsp.h"
#include "../witness_node/hw_model.h"
#include "../witness_node/hw_wire.h"

static std::vector<uint8_t> unhex(const char* s) {
  std::vector<uint8_t> v;
  for (size_t i = 0; s[i] && s[i + 1]; i += 2) { unsigned x; sscanf(s + i, "%2x", &x); v.push_back((uint8_t)x); }
  return v;
}

int main(int argc, char** argv) {
  if (argc < 2) return 2;
  const char* mode = argv[1];
  if (!strcmp(mode, "hmac") && argc == 4) {
    auto key = unhex(argv[2]);
    uint8_t mac[32]; char hex[65];
    hw::hmac_sha256(key.data(), key.size(), (const uint8_t*)argv[3], strlen(argv[3]), mac);
    hw::to_hex(mac, 32, hex);
    puts(hex);
    return 0;
  }
  if (!strcmp(mode, "wire") && argc == 14) {
    auto key = unhex(argv[2]);
    hw::Wire w; memset(&w, 0, sizeof(w));
    strncpy(w.n, argv[3], 16); w.b = strtoul(argv[4], 0, 10); w.s = strtoul(argv[5], 0, 10);
    w.t_ms = strtoll(argv[6], 0, 10); strncpy(w.k, argv[7], 2); w.st = argv[8][0]; w.c = atoi(argv[9]);
    w.ca = argv[10][0]; w.cm = argv[11][0]; w.p = atoi(argv[12]); strncpy(w.f, argv[13], 16);
    char sig[65], json[400];
    hw::sign(w, key.data(), key.size(), sig);
    hw::to_json(w, sig, json, sizeof(json));
    puts(json);
    return 0;
  }
  if (!strcmp(mode, "feat") && argc == 4) {
    const bool acc = !strcmp(argv[2], "acc");
    const int mains = atoi(argv[3]);
    int nblocks = 0;
    if (scanf("%d", &nblocks) != 1) return 2;
    const int n = acc ? hw::ACC_N : hw::MIC_N;
    std::vector<float> a(n), b(n), c(n), re(hw::MIC_N), im(hw::MIC_N);
    hw::BlockStats prev, cur;
    bool have_prev = false;
    for (int blk = 0; blk < nblocks; blk++) {
      for (int i = 0; i < n; i++) if (scanf("%f", &a[i]) != 1) return 2;
      if (acc) {
        for (int i = 0; i < n; i++) if (scanf("%f", &b[i]) != 1) return 2;
        for (int i = 0; i < n; i++) if (scanf("%f", &c[i]) != 1) return 2;
        hw::acc_block(a.data(), b.data(), c.data(), mains, have_prev ? &prev : nullptr, &cur, re.data(), im.data());
      } else {
        hw::mic_block(a.data(), mains, have_prev ? &prev : nullptr, &cur, re.data(), im.data());
      }
      if (have_prev) {
        float f[hw::NFEAT];
        hw::window_features(acc, prev, cur, f);
        for (int i = 0; i < hw::NFEAT; i++) printf("%.6f%c", f[i], i + 1 < hw::NFEAT ? ' ' : '\n');
      }
      prev = cur; have_prev = true;
    }
    return 0;
  }
  if (!strcmp(mode, "calib")) {
    std::vector<float> R, S, Q;
    char tag[8];
    while (scanf("%7s", tag) == 1) {
      float row[hw::NFEAT];
      for (int i = 0; i < hw::NFEAT; i++) if (scanf("%f", &row[i]) != 1) return 2;
      std::vector<float>& dst = tag[0] == 'R' ? R : tag[0] == 'S' ? S : Q;
      dst.insert(dst.end(), row, row + hw::NFEAT);
    }
    hw::Model m;
    hw::CalResult r = hw::calibrate(R.data(), (int)(R.size() / hw::NFEAT), S.data(), (int)(S.size() / hw::NFEAT), m);
    printf("cal %.6f %.6f %.6f %d %.6f\n", r.acc_r, r.acc_s, r.quality, (int)r.verdict, m.d2);
    printf("w"); for (int i = 0; i < hw::NFEAT; i++) printf(" %.6f", m.w[i]); printf("\n");
    printf("m"); for (int i = 0; i < hw::NFEAT; i++) printf(" %.6f", m.m[i]); printf("\n");
    for (size_t q = 0; q + hw::NFEAT <= Q.size(); q += hw::NFEAT) printf("z %.6f\n", m.z(&Q[q]));
    return 0;
  }
  if (!strcmp(mode, "decide")) {
    hw::Decider d;
    float za, zm; int ua, um;
    while (scanf("%f %f %d %d", &za, &zm, &ua, &um) == 4) {
      hw::Decision x = d.step(hw::z_to_state(za, ua), za, hw::z_to_state(zm, um), zm);
      printf("%c %d %c %c %d\n", x.st, x.c, x.ca, x.cm, x.event ? 1 : 0);
    }
    return 0;
  }
  fprintf(stderr, "bad arguments\n");
  return 2;
}
