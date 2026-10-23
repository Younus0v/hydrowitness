// Portable signal processing for the Witness Node. No Arduino or ESP-IDF headers on purpose: this file is
// compiled on a normal computer by tests/test_firmware_core.py and compared number for number with
// training/node_reference.py. If you change a definition here, change it there too.
#pragma once
#include <math.h>
#include <stdint.h>
#include <string.h>

#include "hw_tables.h"

namespace hw {

constexpr int NFEAT = 15;
constexpr int ACC_FS = 1000, ACC_N = 512, ACC_NB = 8;     // MPU6050 accel low-pass is about 260 Hz
constexpr int MIC_FS = 16000, MIC_N = 8192, MIC_NB = 10;
constexpr int MAX_NB = 10;
constexpr double DSP_EPS = 1e-18;   // not "EPS": the ESP32 toolchain's own headers define a macro with that name

struct BlockStats {
  float var[3];          // per-axis variance (acc) or total variance in var[0] (mic)
  float band[MAX_NB];    // band powers
  float m1, m2;          // power within +-2 Hz of 1x and 2x mains
  float spec[MAX_NB];    // band powers normalized to sum 1 (for spectral flux)
  float crest;           // peak / rms of the mean-removed signal (acc: of the vibration magnitude)
  float flux;            // L1 distance between this block's normalized spectrum and the previous block's
};

// ---------------------------------------------------------------------------------------- FFT
// Radix-2 in-place complex FFT with a quarter-wave sine table sized for the largest block (8192).
inline float* sin_table() {
  static float tab[MIC_N / 4 + 1];
  static bool ready = false;
  if (!ready) {
    for (int k = 0; k <= MIC_N / 4; k++) tab[k] = (float)sin(2.0 * M_PI * (double)k / (double)MIC_N);
    ready = true;
  }
  return tab;
}

inline void twiddle(int idx, float& c, float& s) {  // angle = 2*pi*idx/MIC_N, idx in [0, MIC_N/2)
  const float* t = sin_table();
  const int Q = MIC_N / 4;
  if (idx <= Q) { s = t[idx]; c = t[Q - idx]; }
  else { s = t[MIC_N / 2 - idx]; c = -t[idx - Q]; }
}

inline void fft(float* re, float* im, int n) {
  for (int i = 1, j = 0; i < n; i++) {
    int bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) { float t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
  }
  for (int len = 2; len <= n; len <<= 1) {
    const int half = len >> 1, stride = MIC_N / len;
    for (int i = 0; i < n; i += len) {
      for (int k = 0; k < half; k++) {
        float c, s;
        twiddle(k * stride, c, s);
        const float wr = c, wi = -s;
        const float ur = re[i + k], ui = im[i + k];
        const float xr = re[i + k + half], xi = im[i + k + half];
        const float vr = xr * wr - xi * wi, vi = xr * wi + xi * wr;
        re[i + k] = ur + vr;            im[i + k] = ui + vi;
        re[i + k + half] = ur - vr;     im[i + k + half] = ui - vi;
      }
    }
  }
}

// ------------------------------------------------------------------------------- shared helpers
// The window is symmetric, so only half of it is stored. It is built once, in double precision, and kept as
// float: on the ESP32 a double cos() costs tens of microseconds, and calling it per sample for every block
// would not fit in the 512 ms block period.
inline double hann_exact(int i, int n) { return 0.5 - 0.5 * cos(2.0 * M_PI * (double)i / (double)(n - 1)); }

inline const float* hann_half(int n) {  // n is ACC_N or MIC_N
  static float t_acc[ACC_N / 2], t_mic[MIC_N / 2];
  static bool r_acc = false, r_mic = false;
  float* t = n == ACC_N ? t_acc : t_mic;
  bool& ready = n == ACC_N ? r_acc : r_mic;
  if (!ready) {
    for (int k = 0; k < n / 2; k++) t[k] = (float)hann_exact(k, n);
    ready = true;
  }
  return t;
}

inline double hann_at(int i, int n) {
  const float* t = hann_half(n);
  return (double)t[i < n - 1 - i ? i : n - 1 - i];
}

inline double hann_energy(int n) {
  static double e_acc = -1.0, e_mic = -1.0;
  double& e = n == ACC_N ? e_acc : e_mic;
  if (e < 0) {
    double s = 0;
    for (int i = 0; i < n; i++) { const double w = hann_at(i, n); s += w * w; }
    e = s;
  }
  return e;
}

// FFT of one mean-removed, Hann-windowed channel, then add its one-sided power into bands and mains sums.
inline void add_power(const float* x, double mean, int n, const int* kb, int nb, const int* mains,
                      float* re, float* im, float* band, float* m1, float* m2) {
  for (int i = 0; i < n; i++) { re[i] = (float)(((double)x[i] - mean) * hann_at(i, n)); im[i] = 0.f; }
  fft(re, im, n);
  const double scale = 2.0 / ((double)n * hann_energy(n));
  for (int b = 0; b < nb; b++)
    for (int k = kb[b]; k < kb[b + 1]; k++) band[b] += (float)(scale * ((double)re[k] * re[k] + (double)im[k] * im[k]));
  for (int k = mains[0]; k <= mains[1]; k++) *m1 += (float)(scale * ((double)re[k] * re[k] + (double)im[k] * im[k]));
  for (int k = mains[2]; k <= mains[3]; k++) *m2 += (float)(scale * ((double)re[k] * re[k] + (double)im[k] * im[k]));
}

inline void finish(BlockStats& o, int nb, double mag_sq_sum, double mag_max, int n, const BlockStats* prev) {
  double tot = 0;
  for (int b = 0; b < nb; b++) tot += o.band[b];
  for (int b = 0; b < nb; b++) o.spec[b] = tot > 0 ? (float)(o.band[b] / tot) : 0.f;
  const double rms = sqrt(mag_sq_sum / n);
  o.crest = rms > 0 ? (float)(mag_max / rms) : 1.f;
  double fl = 0;
  if (prev) for (int b = 0; b < nb; b++) fl += fabs((double)o.spec[b] - (double)prev->spec[b]);
  o.flux = (float)fl;
}

// re/im are scratch arrays of at least MIC_N floats each (shared by both channels to save RAM).
inline void acc_block(const float* ax, const float* ay, const float* az, int mains_hz, const BlockStats* prev,
                      BlockStats* out, float* re, float* im) {
  const int n = ACC_N;
  const float* ch[3] = {ax, ay, az};
  double mean[3] = {0, 0, 0};
  for (int a = 0; a < 3; a++) { for (int i = 0; i < n; i++) mean[a] += ch[a][i]; mean[a] /= n; }
  memset(out, 0, sizeof(*out));
  double sq[3] = {0, 0, 0}, mag_sq = 0, mag_max = 0;
  for (int i = 0; i < n; i++) {
    double m2 = 0;
    for (int a = 0; a < 3; a++) { const double d = ch[a][i] - mean[a]; sq[a] += d * d; m2 += d * d; }
    mag_sq += m2;
    const double m = sqrt(m2);
    if (m > mag_max) mag_max = m;
  }
  for (int a = 0; a < 3; a++) out->var[a] = (float)(sq[a] / n);
  const int* mains = mains_hz == 50 ? ACC_MAINS50 : ACC_MAINS60;
  for (int a = 0; a < 3; a++) add_power(ch[a], mean[a], n, ACC_KB, ACC_NB, mains, re, im, out->band, &out->m1, &out->m2);
  finish(*out, ACC_NB, mag_sq, mag_max, n, prev);
}

inline void mic_block(const float* x, int mains_hz, const BlockStats* prev, BlockStats* out, float* re, float* im) {
  const int n = MIC_N;
  double mean = 0;
  for (int i = 0; i < n; i++) mean += x[i];
  mean /= n;
  memset(out, 0, sizeof(*out));
  double sq = 0, mag_max = 0;
  for (int i = 0; i < n; i++) {
    const double d = x[i] - mean;
    sq += d * d;
    if (fabs(d) > mag_max) mag_max = fabs(d);
  }
  out->var[0] = (float)(sq / n);
  const int* mains = mains_hz == 50 ? MIC_MAINS50 : MIC_MAINS60;
  add_power(x, mean, n, MIC_KB, MIC_NB, mains, re, im, out->band, &out->m1, &out->m2);
  finish(*out, MIC_NB, sq, mag_max, n, prev);
}

inline float db(double v) { return (float)(10.0 * log10(v + DSP_EPS)); }

// Features of the window made of two consecutive blocks (see FEATURES in training/node_reference.py).
inline void window_features(bool acc, const BlockStats& a, const BlockStats& b, float* f) {
  int i = 0;
  if (acc) { for (int k = 0; k < 3; k++) f[i++] = db(((double)a.var[k] + b.var[k]) / 2.0); }
  else f[i++] = db(((double)a.var[0] + b.var[0]) / 2.0);
  const int nb = acc ? ACC_NB : MIC_NB;
  for (int k = 0; k < nb; k++) f[i++] = db(((double)a.band[k] + b.band[k]) / 2.0);
  f[i++] = db(((double)a.m1 + b.m1) / 2.0);
  f[i++] = db(((double)a.m2 + b.m2) / 2.0);
  f[i++] = (a.flux + b.flux) / 2.0f;
  f[i++] = (a.crest + b.crest) / 2.0f;
}

}  // namespace hw
