"""backtest — verify a trading backtest the way an honest quant would, dependency-free.

The one thing that kills backtests is the same thing that kills benchmark scores: you tried many, and you
reported the best. The rigorous correction for that in finance is the **Deflated Sharpe Ratio** (Bailey &
López de Prado, 2014): given how many strategy variants you tested, what Sharpe would the LUCKIEST zero-skill
strategy have shown — and does yours beat that, after adjusting for non-normal returns and sample length?

Plus the two other quiet killers this author learned live (see the book, Part II): costs that eat the edge,
and fills a real queue would never have given. This module quantifies all three.

Pure `math` — no numpy/scipy — so an agent can call it anywhere.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, asdict

_EULER = 0.5772156649015329


# --------------------------------------------------------------------------- #
# normal CDF + inverse (probit) — self-contained
# --------------------------------------------------------------------------- #
def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def _probit(p: float) -> float:
    """Inverse standard-normal CDF (Acklam), ~1e-9."""
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
# return-series moments
# --------------------------------------------------------------------------- #
def _moments(returns):
    n = len(returns)
    if n < 2:
        raise ValueError("need >=2 returns")
    mu = sum(returns) / n
    m2 = sum((r - mu) ** 2 for r in returns) / n
    sd = math.sqrt(m2)
    if sd == 0:
        return mu, 0.0, 0.0, 3.0
    m3 = sum((r - mu) ** 3 for r in returns) / n
    m4 = sum((r - mu) ** 4 for r in returns) / n
    skew = m3 / sd ** 3
    kurt = m4 / sd ** 4          # non-excess (normal = 3)
    return mu, sd, skew, kurt


def sharpe(returns, rf: float = 0.0) -> float:
    """Per-period Sharpe of a return series (not annualised)."""
    mu, sd, *_ = _moments(returns)
    if sd == 0:
        raise ValueError("zero-variance returns")
    return (mu - rf) / sd


# --------------------------------------------------------------------------- #
# Probabilistic + Deflated Sharpe Ratio
# --------------------------------------------------------------------------- #
def probabilistic_sharpe_ratio(sr: float, T: int, skew: float = 0.0, kurt: float = 3.0,
                               sr_benchmark: float = 0.0) -> float:
    """PSR = P(true SR > sr_benchmark) given observed per-period SR over T obs, adjusting for skew/kurtosis."""
    if T < 2:
        raise ValueError("need T>=2")
    denom = math.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr ** 2)
    if denom <= 0:
        denom = 1e-9
    z = (sr - sr_benchmark) * math.sqrt(T - 1) / denom
    return _norm_cdf(z)


def expected_max_sharpe(n_trials: int, sr_std: float) -> float:
    """Expected Sharpe of the LUCKIEST of n zero-skill trials whose SR estimates have std sr_std.
       (Bailey-López de Prado false-strategy benchmark; the bar a real strategy must clear.)"""
    if n_trials < 1:
        raise ValueError("n_trials>=1")
    if n_trials == 1:
        return 0.0
    z1 = _probit(1 - 1.0 / n_trials)
    z2 = _probit(1 - 1.0 / (n_trials * math.e))
    return sr_std * ((1 - _EULER) * z1 + _EULER * z2)


@dataclass
class BacktestVerdict:
    sr: float                 # observed per-period Sharpe
    n_trials: int
    T: int
    sr_benchmark: float       # deflation bar = expected max of n_trials zero-skill strategies
    psr: float                # P(SR>0), sample+moment adjusted
    dsr: float                # Deflated SR: P(SR > deflation bar), the real verdict
    survives: bool            # dsr > 0.95
    min_track_record_length: float  # T needed for PSR(vs 0) to hit 0.95
    skew: float
    kurt: float

    def __str__(self) -> str:
        v = "SURVIVES" if self.survives else "does NOT survive"
        return (f"SR={self.sr:.3f} over T={self.T}, {self.n_trials} trials tested; "
                f"deflation bar={self.sr_benchmark:.3f}; DSR={self.dsr:.3f} -> {v} deflation "
                f"(needs DSR>0.95). PSR-vs-0={self.psr:.3f}, minTRL={self.min_track_record_length:.0f} obs.")


def deflated_sharpe(returns=None, *, sr: float = None, T: int = None, n_trials: int = 1,
                    skew: float = 0.0, kurt: float = 3.0, sr_std: float = None) -> BacktestVerdict:
    """The verdict: does this Sharpe survive being the best of `n_trials`, given sample size + non-normality?

    Pass either `returns` (a list) OR summary stats (`sr`, `T`, and optionally `skew`,`kurt`).
    `n_trials` = how many strategy/parameter variants were tried before reporting this one (the look-elsewhere
    count — the single most abused number in backtesting). `sr_std` = spread of SR across those trials
    (defaults to the plain 1/sqrt(T) estimator when you can't supply it)."""
    if returns is not None:
        _, _, sk, ku = _moments(returns)
        sr = sharpe(returns); T = len(returns); skew, kurt = sk, ku
    if sr is None or T is None:
        raise ValueError("supply returns, or both sr and T")
    if sr_std is None:
        sr_std = 1.0 / math.sqrt(T - 1)          # SR sampling std under the null
    bar = expected_max_sharpe(n_trials, sr_std)
    dsr = probabilistic_sharpe_ratio(sr, T, skew, kurt, sr_benchmark=bar)
    psr0 = probabilistic_sharpe_ratio(sr, T, skew, kurt, sr_benchmark=0.0)
    # min track record length to reach PSR(vs 0)=0.95
    denom = (1 - skew * sr + (kurt - 1) / 4 * sr ** 2)
    z95 = _probit(0.95)
    mintrl = 1 + denom * (z95 / sr) ** 2 if sr != 0 else float("inf")
    return BacktestVerdict(sr, n_trials, T, bar, psr0, dsr, dsr > 0.95, mintrl, skew, kurt)


# --------------------------------------------------------------------------- #
# cost haircut — how much of the edge survives realistic frictions
# --------------------------------------------------------------------------- #
@dataclass
class CostHaircut:
    gross_sr: float
    net_sr: float
    cost_per_period: float
    sr_lost_pct: float
    still_positive: bool

    def __str__(self) -> str:
        s = "still positive" if self.still_positive else "GONE (net Sharpe <= 0)"
        return (f"gross SR={self.gross_sr:.3f} -> net SR={self.net_sr:.3f} after "
                f"{self.cost_per_period:.4g}/period costs ({self.sr_lost_pct:.0f}% of Sharpe lost) -> {s}")


def cost_haircut(returns, fee_per_trade: float, trades_per_period: float = 1.0) -> CostHaircut:
    """Subtract realistic per-period trading costs from the return series and recompute Sharpe.
       fee_per_trade = round-trip cost as a fraction (e.g. 0.0004 = 4 bps); trades_per_period = turnover."""
    cpp = fee_per_trade * trades_per_period
    gross = sharpe(returns)
    net_returns = [r - cpp for r in returns]
    mu, sd, *_ = _moments(net_returns)
    net = (mu) / sd if sd else 0.0
    lost = 0.0 if gross == 0 else (1 - net / gross) * 100
    return CostHaircut(gross, net, cpp, lost, net > 0)


def _selftest():
    # a strong-looking Sharpe that came from trying MANY strategies should NOT survive deflation
    weak = deflated_sharpe(sr=0.12, T=250, n_trials=100)   # SR 0.12/day-ish, 100 tries
    assert not weak.survives, weak
    # a genuinely strong Sharpe over a long sample with few trials SHOULD survive
    strong = deflated_sharpe(sr=0.15, T=1000, n_trials=1)
    assert strong.survives, strong
    # deflation bar rises with more trials
    assert expected_max_sharpe(1000, 0.05) > expected_max_sharpe(10, 0.05) > 0
    # probit sanity
    assert abs(_probit(0.975) - 1.959964) < 1e-4
    # cost haircut kills a thin edge
    import random
    random.seed  # (no RNG at import; build a deterministic thin-edge series)
    rets = [0.0004 if i % 2 == 0 else -0.0001 for i in range(200)]  # tiny positive drift
    hc = cost_haircut(rets, fee_per_trade=0.0006, trades_per_period=1.0)
    assert hc.gross_sr > 0 and hc.net_sr < hc.gross_sr
    print("backtest selftest: OK (deflation bar, DSR survive/refute, cost haircut)")


if __name__ == "__main__":
    _selftest()
