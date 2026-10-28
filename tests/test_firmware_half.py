"""The install recordings are stored as 16-bit halves on the node (hw_model.h) so that the calibration fits in the ESP32's small free memory.
These tests prove the conversion is exactly IEEE half precision and that calibrating from halves gives the same answer as from floats."""
import os
import shutil
import subprocess

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="needs a C++ compiler")

CPP = r'''
#include <stdio.h>
#include <stdlib.h>
#include <vector>
#include "hw_model.h"
int main(int, char** argv) {
  const char* mode = argv[1];
  if (mode[0] == 'c') {                      // c in.f32 out.u16: float -> half bits
    FILE* f = fopen(argv[2], "rb"); std::vector<float> v; float x;
    while (fread(&x, 4, 1, f) == 1) v.push_back(x);
    fclose(f);
    for (float y : v) printf("%u\n", (unsigned)hw::float_to_half(y));
  } else if (mode[0] == 'h') {               // h: every half bit pattern -> float and back
    int bad = 0;
    for (uint32_t h = 0; h < 65536; h++) {
      const float f = hw::half_to_float((uint16_t)h);
      printf("%u %.9g\n", h, (double)f);
      if (f == f && hw::float_to_half(f) != (uint16_t)h) bad++;
    }
    fprintf(stderr, "bad=%d\n", bad);
  } else {                                    // k rows.f32 nr ns: calibrate from floats and from halves
    FILE* f = fopen(argv[2], "rb"); std::vector<float> v; float x;
    while (fread(&x, 4, 1, f) == 1) v.push_back(x);
    fclose(f);
    const int nr = atoi(argv[3]), ns = atoi(argv[4]);
    std::vector<uint16_t> h(v.size());
    for (size_t i = 0; i < v.size(); i++) h[i] = hw::float_to_half(v[i]);
    hw::Model a, b;
    const hw::CalResult ra = hw::calibrate(v.data(), nr, v.data() + (size_t)nr * hw::NFEAT, ns, a);
    const hw::CalResult rb = hw::calibrate_rows(hw::HalfRows{h.data()}, nr, hw::HalfRows{h.data() + (size_t)nr * hw::NFEAT}, ns, b);
    printf("%d %.6f %d %.6f\n", (int)ra.verdict, ra.quality, (int)rb.verdict, rb.quality);
    for (int i = 0; i < hw::NFEAT; i++) printf("%.9g %.9g %.9g %.9g\n", (double)a.mu_r[i], (double)b.mu_r[i], (double)a.w[i], (double)b.w[i]);
  }
  return 0;
}
'''


@pytest.fixture(scope="module")
def tool(tmp_path_factory):
    d = tmp_path_factory.mktemp("half")
    src = str(d / "half_cli.cpp")
    exe = str(d / "half_cli")
    with open(src, "w", encoding="utf-8") as f:
        f.write(CPP)
    r = subprocess.run(["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.join(ROOT, "firmware", "witness_node"), "-o", exe, src],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return exe


def run(tool, *args):
    r = subprocess.run([tool, *args], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r


def test_every_half_value_survives_a_round_trip_and_matches_numpy(tool):
    r = run(tool, "h")
    assert "bad=0" in r.stderr
    got = np.array([float(line.split()[1]) for line in r.stdout.strip().splitlines()], dtype=np.float32)      # printed with 9 digits: exact for float32
    want = np.arange(65536, dtype=np.uint16).view(np.float16).astype(np.float32)
    both_nan = np.isnan(got) & np.isnan(want)
    assert np.all((got == want) | both_nan)


def test_float_to_half_rounds_exactly_like_numpy_including_subnormals_and_ties(tool, tmp_path):
    rng = np.random.default_rng(7)
    parts = [rng.normal(0, 30, 20000), 10 ** rng.uniform(-9, 5, 20000), -(10 ** rng.uniform(-9, 5, 20000)),
             np.array([0.0, -0.0, 1.0, 65504.0, 65519.0, 65520.0, 1e9, 6.1e-5, 5.96e-8, 2.98e-8, 1.0 + 2 ** -11, 1.0 + 3 * 2 ** -11])]
    x = np.concatenate(parts).astype(np.float32)
    f = tmp_path / "in.f32"
    x.tofile(f)
    got = np.array([int(v) for v in run(tool, "c", str(f)).stdout.split()], dtype=np.uint16)
    with np.errstate(over="ignore"):
        want = x.astype(np.float16).view(np.uint16)
    big = np.abs(x) >= 65520.0                                   # numpy overflows to infinity there; the node saturates instead
    assert np.array_equal(got[~big], want[~big])
    assert np.all((got[big] & 0x7fff) == 0x7bff)


def test_calibrating_from_halves_gives_the_same_verdict_and_nearly_the_same_model(tool, tmp_path):
    rng = np.random.default_rng(3)
    nfeat = 15
    mu_r = rng.uniform(-60, 5, nfeat)
    mu_s = mu_r - rng.uniform(1.5, 8, nfeat)
    for case, (nr, ns) in enumerate([(117, 117), (60, 45)]):
        R = mu_r + rng.normal(0, 0.8, (nr, nfeat))
        S = mu_s + rng.normal(0, 0.8, (ns, nfeat))
        R[:, 13] = np.abs(rng.normal(0.002, 0.0005, nr))         # flux and crest live on very different scales
        S[:, 13] = np.abs(rng.normal(0.0003, 0.0001, ns))
        R[:, 14] = rng.normal(0.5, 0.05, nr)
        S[:, 14] = rng.normal(0.3, 0.05, ns)
        f = tmp_path / f"rows{case}.f32"
        np.concatenate([R, S]).astype(np.float32).tofile(f)
        lines = run(tool, "k", str(f), str(nr), str(ns)).stdout.strip().splitlines()
        va, qa, vb, qb = lines[0].split()
        assert va == vb and abs(float(qa) - float(qb)) <= 0.05
        mu = np.array([[float(v) for v in ln.split()] for ln in lines[1:]])
        assert np.allclose(mu[:, 0], mu[:, 1], rtol=2e-3, atol=2e-3)
        assert np.allclose(mu[:, 2], mu[:, 3], rtol=0.15, atol=1e-3 + 0.15 * np.abs(mu[:, 2]).max() * 0.01)
