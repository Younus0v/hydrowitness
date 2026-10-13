// Witness Node wire format: canonical string, HMAC-SHA256 and the JSON body.
// Must stay byte-identical with hydrowitness/security/nodesig.py (canonical_string / sign / make_wire).
// SHA-256 and HMAC are implemented here so the same code runs on the ESP32 and in the host tests.
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace hw {

// ------------------------------------------------------------------------------------ SHA-256
struct Sha256 {
  uint32_t h[8];
  uint8_t buf[64];
  uint64_t total = 0;
  size_t fill = 0;

  static inline uint32_t rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }

  Sha256() { reset(); }
  void reset() {
    static const uint32_t init[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                     0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    memcpy(h, init, sizeof(h));
    total = 0;
    fill = 0;
  }
  void block(const uint8_t* p) {
    static const uint32_t K[64] = {
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
    uint32_t w[64];
    for (int i = 0; i < 16; i++)
      w[i] = ((uint32_t)p[4 * i] << 24) | ((uint32_t)p[4 * i + 1] << 16) | ((uint32_t)p[4 * i + 2] << 8) | p[4 * i + 3];
    for (int i = 16; i < 64; i++) {
      const uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
      const uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
      w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
    for (int i = 0; i < 64; i++) {
      const uint32_t S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const uint32_t ch = (e & f) ^ (~e & g);
      const uint32_t t1 = hh + S1 + ch + K[i] + w[i];
      const uint32_t S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const uint32_t mj = (a & b) ^ (a & c) ^ (b & c);
      const uint32_t t2 = S0 + mj;
      hh = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
  }
  void update(const uint8_t* data, size_t len) {
    total += len;
    while (len) {
      const size_t take = (64 - fill) < len ? (64 - fill) : len;
      memcpy(buf + fill, data, take);
      fill += take; data += take; len -= take;
      if (fill == 64) { block(buf); fill = 0; }
    }
  }
  void finish(uint8_t out[32]) {
    const uint64_t bits = total * 8;
    uint8_t pad = 0x80;
    update(&pad, 1);
    pad = 0;
    while (fill != 56) update(&pad, 1);
    uint8_t len[8];
    for (int i = 0; i < 8; i++) len[i] = (uint8_t)(bits >> (56 - 8 * i));
    update(len, 8);
    for (int i = 0; i < 8; i++) {
      out[4 * i] = h[i] >> 24; out[4 * i + 1] = h[i] >> 16; out[4 * i + 2] = h[i] >> 8; out[4 * i + 3] = h[i];
    }
  }
};

inline void hmac_sha256(const uint8_t* key, size_t klen, const uint8_t* msg, size_t mlen, uint8_t out[32]) {
  uint8_t k[64] = {0};
  if (klen > 64) { Sha256 s; s.update(key, klen); s.finish(k); }
  else memcpy(k, key, klen);
  uint8_t ipad[64], opad[64];
  for (int i = 0; i < 64; i++) { ipad[i] = k[i] ^ 0x36; opad[i] = k[i] ^ 0x5c; }
  uint8_t inner[32];
  Sha256 a;
  a.update(ipad, 64);
  a.update(msg, mlen);
  a.finish(inner);
  Sha256 b;
  b.update(opad, 64);
  b.update(inner, 32);
  b.finish(out);
}

inline void to_hex(const uint8_t* d, size_t n, char* out) {  // out needs 2n+1 bytes
  static const char* H = "0123456789abcdef";
  for (size_t i = 0; i < n; i++) { out[2 * i] = H[d[i] >> 4]; out[2 * i + 1] = H[d[i] & 15]; }
  out[2 * n] = 0;
}

// ------------------------------------------------------------------------------------ messages
struct Wire {
  char n[17];       // node id
  uint32_t b;       // boot counter (kept in flash)
  uint32_t s;       // sequence number within this boot
  int64_t t_ms;     // NTP time, milliseconds since the epoch
  char k[3];        // "hb" heartbeat or "ev" event
  char st;          // R, S, T or U
  int c;            // confidence, permille
  char ca, cm;      // accelerometer and microphone channel states (R, S or U)
  int p;            // 1 = mains power OK, 0 = on backup
  char f[17];       // firmware hash (short hex)
};

// "n|b|s|t|k|st|c|ca|cm|p|f", the exact string signed by the node and verified by the server.
inline int canonical(const Wire& w, char* out, size_t cap) {
  return snprintf(out, cap, "%s|%lu|%lu|%lld|%s|%c|%d|%c|%c|%d|%s", w.n, (unsigned long)w.b, (unsigned long)w.s,
                  (long long)w.t_ms, w.k, w.st, w.c, w.ca, w.cm, w.p, w.f);
}

inline void sign(const Wire& w, const uint8_t* key, size_t klen, char sig_hex[65]) {
  char msg[160];
  const int n = canonical(w, msg, sizeof(msg));
  uint8_t mac[32];
  hmac_sha256(key, klen, (const uint8_t*)msg, (size_t)n, mac);
  to_hex(mac, 32, sig_hex);
}

inline int to_json(const Wire& w, const char* sig_hex, char* out, size_t cap) {
  return snprintf(out, cap,
                  "{\"n\":\"%s\",\"b\":%lu,\"s\":%lu,\"t\":%lld,\"k\":\"%s\",\"st\":\"%c\",\"c\":%d,\"ca\":\"%c\","
                  "\"cm\":\"%c\",\"p\":%d,\"f\":\"%s\",\"sig\":\"%s\"}",
                  w.n, (unsigned long)w.b, (unsigned long)w.s, (long long)w.t_ms, w.k, w.st, w.c, w.ca, w.cm, w.p,
                  w.f, sig_hex);
}

}  // namespace hw
