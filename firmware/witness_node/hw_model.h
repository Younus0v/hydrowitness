// Portable classifier, install calibration and persistence logic for the Witness Node.
// Mirrors the Model / calibrate / z_to_state definitions in training/node_reference.py.
#pragma once
#include <math.h>
#include <stdint.h>
#include <string.h>

#include "hw_dsp.h"

namespace hw {

constexpr float DB_FLOOR = 0.25f, FLUX_FLOOR = 1e-4f, CREST_FLOOR = 1e-2f;
constexpr float FIT_FRACTION = 0.6f;
constexpr float GOOD_ACCURACY = 0.97f, FAIR_ACCURACY = 0.90f;
constexpr float Z_THRESHOLD = 0.35f;
constexpr uint32_t MODEL_MAGIC = 0x48574d31;  // "HWM1": bump when the layout changes

enum Verdict { POOR = 0, FAIR = 1, GOOD = 2 };

struct Model {
  uint32_t magic;
  float mu_r[NFEAT], mu_s[NFEAT], var[NFEAT], w[NFEAT], m[NFEAT];
  float d2;

  bool valid() const { return magic == MODEL_MAGIC && d2 > 1e-6f; }

  // About +1 at a typical RUNNING window, about -1 at a typical STOPPED window.
  float z(const float* x) const {
    if (!valid()) return 0.f;
    double s = 0;
    for (int i = 0; i < NFEAT; i++) s += (double)w[i] * ((double)x[i] - (double)m[i]);
    return (float)(s / ((double)d2 / 2.0));
  }

  void derive() {  // recompute w, m, d2 from mu_r, mu_s, var
    double d = 0;
    for (int i = 0; i < NFEAT; i++) {
      w[i] = (mu_r[i] - mu_s[i]) / var[i];
      m[i] = (mu_r[i] + mu_s[i]) / 2.f;
      d += (double)(mu_r[i] - mu_s[i]) * (mu_r[i] - mu_s[i]) / var[i];
    }
    d2 = (float)d;
    magic = MODEL_MAGIC;
  }
};

inline float variance_floor(int i) { return i == 13 ? FLUX_FLOOR : i == 14 ? CREST_FLOOR : DB_FLOOR; }

// R and S are row-major arrays of nr / ns feature vectors.
inline void fit(const float* R, int nr, const float* S, int ns, Model& out) {
  memset(&out, 0, sizeof(out));
  for (int i = 0; i < NFEAT; i++) {
    double sr = 0, ss = 0, qr = 0, qs = 0;
    for (int r = 0; r < nr; r++) { const double v = R[r * NFEAT + i]; sr += v; qr += v * v; }
    for (int s = 0; s < ns; s++) { const double v = S[s * NFEAT + i]; ss += v; qs += v * v; }
    const double mr = nr ? sr / nr : 0, ms = ns ? ss / ns : 0;
    const double vr = nr ? qr / nr - mr * mr : 0, vs = ns ? qs / ns - ms * ms : 0;
    out.mu_r[i] = (float)mr;
    out.mu_s[i] = (float)ms;
    out.var[i] = (float)((fmax(vr, 0.0) + fmax(vs, 0.0)) / 2.0 + variance_floor(i));
  }
  out.derive();
}

struct CalResult {
  float acc_r, acc_s, quality;
  Verdict verdict;
};

// Fit on the first 60% of each class, test on the last 40%, then refit on all windows.
inline CalResult calibrate(const float* R, int nr, const float* S, int ns, Model& out) {
  CalResult res = {0, 0, 0, POOR};
  int fr = (int)(nr * FIT_FRACTION), fs = (int)(ns * FIT_FRACTION);
  if (fr < 2) fr = 2;
  if (fs < 2) fs = 2;
  if (nr < 4 || ns < 4) { memset(&out, 0, sizeof(out)); return res; }
  Model trial;
  fit(R, fr, S, fs, trial);
  int okr = 0, oks = 0;
  for (int i = fr; i < nr; i++) if (trial.z(R + i * NFEAT) > 0) okr++;
  for (int i = fs; i < ns; i++) if (trial.z(S + i * NFEAT) < 0) oks++;
  res.acc_r = nr > fr ? (float)okr / (nr - fr) : 0.f;
  res.acc_s = ns > fs ? (float)oks / (ns - fs) : 0.f;
  res.quality = res.acc_r < res.acc_s ? res.acc_r : res.acc_s;
  res.verdict = res.quality >= GOOD_ACCURACY ? GOOD : res.quality >= FAIR_ACCURACY ? FAIR : POOR;
  fit(R, nr, S, ns, out);
  return res;
}

// Slow drift: only called after an operator confirms the state, so the node never trains itself on a
// possibly wrong state (project document 4.4). Moves the matching class mean a small step toward x.
inline void adapt(Model& m, const float* x, bool running, float alpha) {
  float* mu = running ? m.mu_r : m.mu_s;
  for (int i = 0; i < NFEAT; i++) mu[i] += alpha * (x[i] - mu[i]);
  m.derive();
}

inline char z_to_state(float z, bool usable) {
  if (!usable) return 'U';
  return z > Z_THRESHOLD ? 'R' : z < -Z_THRESHOLD ? 'S' : 'U';
}

// ---------------------------------------------------------------------------------- persistence
// A window decision per channel is smoothed by a 3-of-5 vote; the combined state must then repeat for
// three blocks before the node reports a change. A single odd window can't flip the reported state.
struct ChannelFilter {
  static constexpr int N = 5;
  char hist[N];
  float zabs[N];
  int count = 0, pos = 0;

  void push(char raw, float z) {
    hist[pos] = raw;
    zabs[pos] = fabsf(z);
    pos = (pos + 1) % N;
    if (count < N) count++;
  }
  char state() const {
    if (count < N) return 'U';
    int r = 0, s = 0;
    for (int i = 0; i < N; i++) { if (hist[i] == 'R') r++; else if (hist[i] == 'S') s++; }
    return r >= 3 ? 'R' : s >= 3 ? 'S' : 'U';
  }
  float zabs_avg() const {
    if (!count) return 0.f;
    float s = 0;
    for (int i = 0; i < count; i++) s += zabs[i];
    return s / count;
  }
};

struct Decision {
  char st;       // R, S or U: the reported state
  int c;         // confidence in permille
  char ca, cm;   // smoothed channel states
  bool event;    // true when st just changed
};

class Decider {
 public:
  static constexpr int PERSIST = 3;

  Decision step(char raw_a, float za, char raw_m, float zm) {
    fa_.push(raw_a, za);
    fm_.push(raw_m, zm);
    const char sa = fa_.state(), sm = fm_.state();
    const bool ka = sa != 'U', km = sm != 'U';
    char st = 'U';
    int c = 200;
    const float qa = fminf(1.f, fa_.zabs_avg()), qm = fminf(1.f, fm_.zabs_avg());
    if (ka && km) {
      if (sa == sm) { st = sa; c = 850 + (int)lroundf(125.f * fminf(qa, qm)); }
      else { st = 'U'; c = 300; }                                   // the channels contradict: say so
    } else if (ka || km) {
      st = ka ? sa : sm;
      c = 500 + (int)lroundf(260.f * (ka ? qa : qm));              // one channel only: capped confidence
    }
    bool event = false;
    if (st == decided_) cand_n_ = 0;
    else {
      if (st == cand_) cand_n_++; else { cand_ = st; cand_n_ = 1; }
      if (cand_n_ >= PERSIST) { decided_ = st; cand_n_ = 0; event = true; }
    }
    const int conf = st == decided_ ? c : (c < 400 ? c : 400);
    return Decision{decided_, conf, sa, sm, event};
  }
  char decided() const { return decided_; }
  void reset() { *this = Decider(); }

 private:
  ChannelFilter fa_, fm_;
  char decided_ = 'U', cand_ = 'U';
  int cand_n_ = 0;
};

}  // namespace hw
