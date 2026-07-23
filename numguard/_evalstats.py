"""_evalstats — the handful of eval statistics numguard needs, vendored self-contained.

Same textbook checks as the `evalgate` library, copied in so numguard installs anywhere with no git
dependency (a git+https dep breaks Docker builds). Pure `math`. If you also run the full evalgate library,
these agree with it.
"""
from __future__ import annotations
import math
from dataclasses import dataclass


# --------------------------------------------------------------------------- #
# probit / normal
# --------------------------------------------------------------------------- #
def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def probit(p: float) -> float:
    if not 0.0 < p < 1.0:
        raise ValueError("need 0<p<1")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= phigh:
        q = p - 0.5; r = q*q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


# --------------------------------------------------------------------------- #
# multiple comparisons
# --------------------------------------------------------------------------- #
def sidak(p: float, n: int) -> float:
    if not 0.0 <= p <= 1.0 or n < 1:
        raise ValueError("need 0<=p<=1 and n>=1")
    return 1.0 - (1.0 - p) ** n


def bonferroni(p: float, n: int) -> float:
    if not 0.0 <= p <= 1.0 or n < 1:
        raise ValueError("need 0<=p<=1 and n>=1")
    return min(1.0, p * n)


@dataclass
class Correction:
    raw_p: float
    n_tested: int
    corrected_p: float
    method: str
    significant: bool
    alpha: float


def correct_best_of(p: float, n_tested: int, method: str = "sidak", alpha: float = 0.05) -> Correction:
    corrected = {"sidak": sidak, "bonferroni": bonferroni}[method](p, n_tested)
    return Correction(p, n_tested, corrected, method, corrected < alpha, alpha)


# --------------------------------------------------------------------------- #
# binomial / judge bias
# --------------------------------------------------------------------------- #
def binomial_test(k: int, n: int, p0: float = 0.5) -> float:
    if n < 1 or not 0 <= k <= n:
        raise ValueError("need n>=1 and 0<=k<=n")
    if n <= 2000:
        pk = math.comb(n, k) * p0 ** k * (1 - p0) ** (n - k)
        tol = pk * (1 + 1e-9)
        total = 0.0
        for i in range(n + 1):
            pi = math.comb(n, i) * p0 ** i * (1 - p0) ** (n - i)
            if pi <= tol:
                total += pi
        return min(1.0, total)
    mu, sd = n * p0, math.sqrt(n * p0 * (1 - p0))
    z = (abs(k - mu) - 0.5) / sd
    return max(0.0, min(1.0, math.erfc(z / math.sqrt(2))))


@dataclass
class Bias:
    wins: int
    total: int
    rate: float
    p_value: float
    biased: bool
    label: str


def bias_rate(wins: int, total: int, p0: float = 0.5, alpha: float = 0.05,
              label: str = "preferred side wins") -> Bias:
    rate = wins / total
    p = binomial_test(wins, total, p0)
    return Bias(wins, total, rate, p, p < alpha, label)


# --------------------------------------------------------------------------- #
# power / minimum detectable effect
# --------------------------------------------------------------------------- #
def min_detectable_effect(n: int, p_base: float = 0.5, alpha: float = 0.05, power: float = 0.8) -> float:
    if n < 1 or not 0.0 < p_base < 1.0:
        raise ValueError("need n>=1 and 0<p_base<1")
    z = probit(1 - alpha / 2) + probit(power)
    return z * math.sqrt(2 * p_base * (1 - p_base) / n)


@dataclass
class Power:
    n: int
    p1: float
    p2: float
    diff: float
    p_value: float
    significant: bool
    mde: float
    resolvable: bool
    alpha: float
    power: float


def power_check(n: int, p1: float, p2: float, alpha: float = 0.05, power: float = 0.8) -> Power:
    if n < 1 or not (0.0 <= p1 <= 1.0 and 0.0 <= p2 <= 1.0):
        raise ValueError("need n>=1 and 0<=p<=1")
    diff = p1 - p2
    p_bar = (p1 + p2) / 2
    se = math.sqrt(2 * p_bar * (1 - p_bar) / n) if 0 < p_bar < 1 else 0.0
    if se == 0:
        pval = 0.0 if diff != 0 else 1.0
    else:
        z = abs(diff) / se
        pval = min(1.0, math.erfc(z / math.sqrt(2)))
    mde = min_detectable_effect(n, p_bar if 0 < p_bar < 1 else 0.5, alpha, power)
    return Power(n, p1, p2, diff, pval, pval < alpha, mde, abs(diff) >= mde, alpha, power)
