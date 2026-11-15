"""Exact confidence intervals for counts, with no SciPy needed.

Used by the results reports so that a count such as "0 unsafe in 200 systems" is always shown together with what that count can
and cannot rule out. "0 of 200" does not mean "never": the exact 95% upper bound is 1.8%.

  clopper_pearson(k, n)   exact two-sided interval for a proportion (conservative: it never claims more certainty than it has)
  wilson(k, n)            the usual approximate interval, for comparison
  poisson_upper(k, T)     exact two-sided 95% upper bound on an event rate after k events in T units of exposure (site-days)
"""
from __future__ import annotations

import math

Z95 = 1.959963984540054


def _log_pmf(i: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 0.0 if i == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if i == n else -math.inf
    return (math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
            + i * math.log(p) + (n - i) * math.log1p(-p))


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return min(1.0, sum(math.exp(_log_pmf(i, n, p)) for i in range(0, k + 1)))


def _bisect(f, lo: float, hi: float, target: float, increasing: bool, iters: int = 200) -> float:
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        v = f(mid)
        if (v < target) == increasing:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def clopper_pearson(k: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    """Exact (Clopper-Pearson) interval for k successes in n trials."""
    if n <= 0:
        return (0.0, 1.0)
    if not 0 <= k <= n:
        raise ValueError("k must be between 0 and n")
    a = (1.0 - conf) / 2.0
    # lower: P(X >= k | p) = a, which rises with p;  upper: P(X <= k | p) = a, which falls with p
    lo = 0.0 if k == 0 else _bisect(lambda p: 1.0 - binom_cdf(k - 1, n, p), 0.0, 1.0, a, increasing=True)
    hi = 1.0 if k == n else _bisect(lambda p: binom_cdf(k, n, p), 0.0, 1.0, a, increasing=False)
    return (lo, hi)


def wilson(k: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    z = Z95 if abs(conf - 0.95) < 1e-9 else _z(conf)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _z(conf: float) -> float:
    # inverse normal CDF by bisection on erf; only used for non-95% levels
    target = (1 + conf) / 2
    lo, hi = 0.0, 10.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if 0.5 * (1 + math.erf(mid / math.sqrt(2))) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def poisson_upper(k: int, exposure: float, conf: float = 0.95) -> float:
    """Exact upper bound on the event rate (events per unit of exposure) after k events in `exposure` units."""
    if exposure <= 0:
        return math.inf
    a = (1.0 - conf) / 2.0

    def cdf(mu: float) -> float:
        term = math.exp(-mu)
        total = term
        for i in range(1, k + 1):
            term *= mu / i
            total += term
        return total

    hi = max(10.0, 5.0 * (k + 1))
    mu = _bisect(cdf, 0.0, hi, a, increasing=False)
    return mu / exposure


def pct(x: float, digits: int = 1) -> str:
    return f"{100.0 * x:.{digits}f}%"


def show(k: int, n: int, digits: int = 1) -> str:
    """'0 of 200 (0.0%, 95% CI 0.0% to 1.8%)'"""
    lo, hi = clopper_pearson(k, n)
    return f"{k} of {n} ({pct(k / n, digits) if n else 'n/a'}, 95% CI {pct(lo, digits)} to {pct(hi, digits)})"
