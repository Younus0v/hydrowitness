"""SYNTHETIC pump-like signals for testing the Witness Node pipeline.

These are NOT recordings and NOT evidence that the node works on a real pump. They exist so that the
feature extraction, the install calibration, the persistence logic and the C++/Python parity can be
tested before any hardware arrives. Real separability is measured in validation V3 with a real node on
a real pump (see docs/HARDWARE_STAGE.md).

A running pump is modelled as: tonal vibration at the shaft speed and its harmonics, mains hum at 2x the
supply frequency, and broadband flow noise, all on top of the same background as when it is off.
`strength` scales everything the pump adds, so strength=0 is a node mounted somewhere it can't feel the
pump (the "move me" case) and small values are a marginal mounting.
"""
from __future__ import annotations

import numpy as np

ACC_FS, MIC_FS = 1000, 16000


def _shaft_hz(mains: int) -> float:
    return mains / 2.0 * 0.985                      # a 4-pole motor runs slightly below synchronous speed


def acc_signal(running: bool, seconds: float, rng: np.random.Generator, *, strength: float = 1.0,
               mains: int = 60) -> np.ndarray:
    """Returns shape (3, n) in units of g (gravity on z)."""
    n = int(seconds * ACC_FS)
    t = np.arange(n) / ACC_FS
    sig = rng.normal(0.0, 0.0025, (3, n))                              # sensor noise
    sig[2] += 1.0                                                      # gravity
    sig += 0.0015 * np.cumsum(rng.normal(0, 1, (3, n)), axis=1) / np.sqrt(ACC_FS)   # slow building sway
    if running:
        f0 = _shaft_hz(mains)
        for h, amp in ((1, 0.012), (2, 0.020), (3, 0.008)):
            ph = rng.uniform(0, 2 * np.pi, 3)
            for ax, gain in enumerate((1.0, 0.7, 1.3)):
                sig[ax] += strength * gain * amp * np.sin(2 * np.pi * h * f0 * t + ph[ax])
        sig += strength * 0.006 * np.sin(2 * np.pi * 2 * mains * t + rng.uniform(0, 6.28))[None, :]
        sig += strength * rng.normal(0.0, 0.006, (3, n))               # turbulence
    return sig.astype(np.float32)


def mic_signal(running: bool, seconds: float, rng: np.random.Generator, *, strength: float = 1.0,
               mains: int = 60) -> np.ndarray:
    """Returns shape (n,) in units of full scale (about +-1)."""
    n = int(seconds * MIC_FS)
    t = np.arange(n) / MIC_FS
    white = rng.normal(0, 1, n)
    spec = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, 1 / MIC_FS)
    spec *= 1.0 / np.sqrt(np.maximum(freqs, 30.0) / 30.0)             # pink-ish room noise
    sig = np.fft.irfft(spec, n) * 3e-4
    sig += 2e-4 * np.sin(2 * np.pi * mains * t + rng.uniform(0, 6.28))   # mains hum is always there
    sig += 1e-4 * np.sin(2 * np.pi * 2 * mains * t + rng.uniform(0, 6.28))
    if running:
        f0 = _shaft_hz(mains)
        for h, amp in ((1, 6e-4), (2, 1.0e-3), (3, 5e-4), (6, 4e-4)):
            sig += strength * amp * np.sin(2 * np.pi * h * f0 * t + rng.uniform(0, 6.28))
        sig += strength * 1.6e-3 * np.sin(2 * np.pi * 2 * mains * t + rng.uniform(0, 6.28))
        flow = rng.normal(0, 1, n)
        fs_ = np.fft.rfft(flow)
        fs_ *= np.exp(-0.5 * ((np.log(np.maximum(freqs, 50.0)) - np.log(1800.0)) / 0.9) ** 2)   # mid-band hiss
        sig += strength * np.fft.irfft(fs_, n) * 2.5e-3
    return np.clip(sig, -0.99, 0.99).astype(np.float32)


def stream(schedule, rng: np.random.Generator, *, strength_acc: float = 1.0, strength_mic: float = 1.0,
           mains: int = 60) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """schedule = [(running, seconds), ...]. Returns (acc (3,n), mic (m,), truth per second)."""
    accs, mics, truth = [], [], []
    for running, secs in schedule:
        accs.append(acc_signal(running, secs, rng, strength=strength_acc, mains=mains))
        mics.append(mic_signal(running, secs, rng, strength=strength_mic, mains=mains))
        truth += [running] * int(secs)
    return np.concatenate(accs, axis=1), np.concatenate(mics), np.array(truth)
