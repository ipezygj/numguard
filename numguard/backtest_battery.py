"""backtest_battery — the checks a Deflated-Sharpe pass STILL misses.

DSR catches the best-of-N problem. It does not catch same-bar look-ahead, autocorrelation inflating the Sharpe,
regime dependence, tail/drawdown fantasy, one-lucky-epoch fragility, or overfitting beyond the reported number
of trials. This module adds that battery — all on the returns series (and optionally the positions / trade
list), pure-stdlib (no numpy), so an agent can pass numbers and get a structural "is this backtest real?"
verdict. Motivated by a god-tier-quant consult (Kimi), implemented and verified here.

Everything is per-period unless a `periods_per_year` is given. `run_battery(...)` orchestrates and returns a
combined verdict with a flag list.
"""
from __future__ import annotations
import math
import random
from typing import Optional, Sequence

from .backtest import _norm_cdf, _probit, sharpe as _sharpe


# --------------------------------------------------------------------------- #
# small stdlib stats helpers
# --------------------------------------------------------------------------- #
def _mean(x: Sequence[float]) -> float:
    return sum(x) / len(x) if x else 0.0


def _std(x: Sequence[float], ddof: int = 1) -> float:
    n = len(x)
    if n <= ddof:
        return 0.0
    m = _mean(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (n - ddof))


def _autocov(x: Sequence[float], k: int) -> float:
    n = len(x)
    if k >= n:
        return 0.0
    m = _mean(x)
    return sum((x[t] - m) * (x[t - k] - m) for t in range(k, n)) / n


def _autocorr(x: Sequence[float], k: int) -> float:
    g0 = _autocov(x, 0)
    return _autocov(x, k) / g0 if g0 > 0 else 0.0


def _corr(a: Sequence[float], b: Sequence[float]) -> float:
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    a, b = a[:n], b[:n]
    ma, mb = _mean(a), _mean(b)
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = math.sqrt(sum((a[i] - ma) ** 2 for i in range(n)))
    db = math.sqrt(sum((b[i] - mb) ** 2 for i in range(n)))
    return num / (da * db) if da > 0 and db > 0 else 0.0


# --------------------------------------------------------------------------- #
# S-tier
# --------------------------------------------------------------------------- #
def hac_sharpe(returns: Sequence[float], lags: Optional[int] = None) -> dict:
    """Newey–West (HAC) adjusted Sharpe. Positive autocorrelation (smoothing, stale marks, overlapping labels)
    inflates the naive Sharpe by understating variance; the HAC long-run variance corrects it and yields an
    effective sample size. `inflation` > ~1.25 means the headline Sharpe is autocorrelation-assisted."""
    n = len(returns)
    if n < 8:
        return {"error": "need >= 8 returns"}
    m = lags if lags is not None else max(1, int(4 * (n / 100.0) ** (2.0 / 9.0)))
    g0 = _autocov(returns, 0)
    if g0 <= 0:
        return {"error": "zero-variance returns"}
    lrv = g0 + 2.0 * sum((1.0 - k / (m + 1.0)) * _autocov(returns, k) for k in range(1, m + 1))
    lrv = max(lrv, 1e-18)
    mu = _mean(returns)
    sr_naive = mu / math.sqrt(g0)
    sr_hac = mu / math.sqrt(lrv)                       # long-run-variance Sharpe
    ess = n * g0 / lrv                                 # effective sample size (var(mean)=lrv/n=g0/ess)
    t_hac = mu / math.sqrt(lrv / n)
    p = 2 * (1 - _norm_cdf(abs(t_hac)))
    infl = sr_naive / sr_hac if sr_hac != 0 else float("inf")
    return {"sharpe": round(sr_naive, 4), "sharpe_hac": round(sr_hac, 4),
            "effective_sample_size": round(ess, 1), "lags": m, "t_hac": round(t_hac, 3),
            "p_value": round(p, 4), "inflation": round(infl, 3),
            "flag": bool(infl > 1.25 or _autocorr(returns, 1) > 0.2)}


def leakage_scan(positions: Sequence[float], asset_returns: Sequence[float]) -> dict:
    """Same-bar look-ahead detector. A legit strategy sets position p_{t-1} and EARNS a_t; if the reported P&L
    uses p_t·a_t (position and the return it 'predicts' from the same bar) the edge is look-ahead. Compares the
    Sharpe of contemporaneous vs lagged application, plus corr(p_t, a_t) vs corr(p_{t-1}, a_t)."""
    n = min(len(positions), len(asset_returns))
    if n < 8:
        return {"error": "need >= 8 aligned points"}
    p, a = positions[:n], asset_returns[:n]
    contemp = [p[t] * a[t] for t in range(n)]                    # p_t · a_t (suspicious)
    lagged = [p[t - 1] * a[t] for t in range(1, n)]              # p_{t-1} · a_t (honest)
    sr_c = _sharpe(contemp) or 0.0
    sr_l = _sharpe(lagged) or 0.0
    lscore = (sr_c - sr_l) / (abs(sr_l) + 1e-9)
    rho0 = _corr(p, a)                                           # position vs same-bar return
    rho1 = _corr([p[t] for t in range(n - 1)], [a[t + 1] for t in range(n - 1)])  # position vs NEXT return
    z1 = math.atanh(max(-0.999, min(0.999, rho1))) * math.sqrt(max(1, n - 3))
    return {"leakage_score": round(lscore, 3), "sharpe_contemporaneous": round(sr_c, 3),
            "sharpe_lagged": round(sr_l, 3), "corr_same_bar": round(rho0, 3),
            "corr_next_bar": round(rho1, 3), "z_next": round(z1, 2),
            "flag": bool(lscore > 0.5 and abs(rho0) > abs(rho1) + 0.1)}


def regime_stability(returns: Sequence[float], n_blocks: int = 6) -> dict:
    """Cherry-picked-window / regime-dependence detector: per-block Sharpe, the worst block, the fraction of
    blocks that lose, a CUSUM structural-break test, and a first-half vs second-half Sharpe gap."""
    n = len(returns)
    if n < 2 * n_blocks:
        return {"error": f"need >= {2 * n_blocks} returns"}
    sz = n // n_blocks
    block_srs = [(_sharpe(returns[i * sz:(i + 1) * sz]) or 0.0) for i in range(n_blocks)]
    neg_frac = sum(1 for s in block_srs if s < 0) / n_blocks
    # CUSUM of standardized demeaned returns
    mu, sd = _mean(returns), _std(returns)
    cum, peak, brk = 0.0, 0.0, 0
    if sd > 0:
        for t in range(n):
            cum += (returns[t] - mu) / sd
            if abs(cum) / math.sqrt(n) > peak:
                peak, brk = abs(cum) / math.sqrt(n), t
    boundary = math.sqrt(2 * math.log(math.log(max(3, n))))     # Chu–Stinchcombe–White-ish
    half = n // 2
    sr1, sr2 = (_sharpe(returns[:half]) or 0.0), (_sharpe(returns[half:]) or 0.0)
    return {"min_block_sharpe": round(min(block_srs), 3), "block_sharpes": [round(s, 2) for s in block_srs],
            "negative_block_fraction": round(neg_frac, 2), "cusum_stat": round(peak, 3),
            "cusum_boundary": round(boundary, 3), "break_index": brk if peak > boundary else None,
            "half1_sharpe": round(sr1, 3), "half2_sharpe": round(sr2, 3),
            "flag": bool(peak > boundary or neg_frac >= 0.5 or min(block_srs) < 0)}


def pbo(candidates: Sequence[Sequence[float]], n_groups: int = 6) -> dict:
    """Probability of Backtest Overfitting via Combinatorially-Symmetric CV (Bailey/López de Prado). Needs a
    MATRIX of candidate strategies (the ones you selected the winner from) as rows of aligned returns. Splits
    time into `n_groups`, and over every balanced train/test partition ranks in-sample vs out-of-sample; PBO is
    the probability the in-sample winner is below the out-of-sample median."""
    S = len(candidates)
    if S < 2:
        return {"error": "need >= 2 candidate strategies (rows)"}
    T = min(len(c) for c in candidates)
    if T < 2 * n_groups:
        return {"error": f"need >= {2 * n_groups} columns"}
    import itertools
    gsz = T // n_groups
    groups = [list(range(i * gsz, (i + 1) * gsz)) for i in range(n_groups)]
    lambdas = []
    for train_g in itertools.combinations(range(n_groups), n_groups // 2):
        tr = [i for g in train_g for i in groups[g]]
        te = [i for g in range(n_groups) if g not in train_g for i in groups[g]]
        sr_is = [_sharpe([c[i] for i in tr]) or 0.0 for c in candidates]
        sr_oos = [_sharpe([c[i] for i in te]) or 0.0 for c in candidates]
        best = max(range(S), key=lambda s: sr_is[s])            # in-sample winner
        # OOS rank of the IS winner (1=worst..S=best) -> relative rank -> logit
        rank = 1 + sum(1 for s in range(S) if sr_oos[s] < sr_oos[best])
        r = rank / (S + 1.0)
        lambdas.append(math.log(r / (1.0 - r)))
    mu, sd = _mean(lambdas), _std(lambdas)
    prob = _norm_cdf(-mu / sd) if sd > 0 else (1.0 if mu <= 0 else 0.0)
    return {"pbo": round(prob, 3), "n_candidates": S, "n_splits": len(lambdas),
            "flag": bool(prob > 0.5)}


# --------------------------------------------------------------------------- #
# A-tier
# --------------------------------------------------------------------------- #
def drawdown_stats(returns: Sequence[float], periods_per_year: int = 252) -> dict:
    """Path/tail realism: max drawdown, Calmar, 5% CVaR + STARR, and the realized max drawdown vs the drawdown
    a Brownian path with the SAME Sharpe would produce — a realized DD far *smaller* than expected flags
    smoothed / marked returns; far larger flags a hidden left tail."""
    n = len(returns)
    if n < 8:
        return {"error": "need >= 8 returns"}
    eq, peak, mdd = 0.0, 0.0, 0.0
    for r in returns:
        eq += r
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    mu, sd = _mean(returns), _std(returns)
    ann_ret = mu * periods_per_year
    calmar = ann_ret / mdd if mdd > 0 else float("inf")
    tail = sorted(returns)[:max(1, n // 20)]                    # worst 5%
    cvar = -_mean(tail)
    starr = ann_ret / (cvar * math.sqrt(periods_per_year)) if cvar > 0 else float("inf")
    # expected max drawdown of a driftless-adjusted random walk with this per-period sharpe (rough closed form)
    sr = mu / sd if sd > 0 else 0.0
    exp_mdd = sd * (0.63519 + 0.5 * math.log(n) + math.log(abs(sr) + 1e-9)) if sr > 0 else sd * math.sqrt(n)
    ratio = mdd / exp_mdd if exp_mdd > 0 else float("inf")
    return {"max_drawdown": round(mdd, 4), "calmar": round(calmar, 3), "cvar_5pct": round(cvar, 5),
            "starr": round(starr, 3), "expected_max_dd": round(exp_mdd, 4), "dd_ratio": round(ratio, 3),
            "flag": bool(ratio < 0.5 or ratio > 2.0)}          # too-clean or too-ugly vs the Sharpe implies


def bootstrap_stability(returns: Sequence[float], n_boot: int = 1000, block: int = 0, seed: int = 0) -> dict:
    """Stationary block-bootstrap Sharpe distribution: 95% CI and the probability the Sharpe is actually <= 0.
    A strategy whose edge lives in one lucky epoch has a wide CI that straddles zero."""
    n = len(returns)
    if n < 16:
        return {"error": "need >= 16 returns"}
    block = block or max(2, int(round(n ** (1.0 / 3.0))))       # ~ optimal block length
    rng = random.Random(seed)
    srs = []
    for _ in range(n_boot):
        sample = []
        while len(sample) < n:
            start = rng.randrange(n)
            for j in range(block):
                sample.append(returns[(start + j) % n])
        srs.append(_sharpe(sample[:n]) or 0.0)
    srs.sort()
    lo = srs[int(0.025 * n_boot)]
    hi = srs[int(0.975 * n_boot)]
    p_neg = sum(1 for s in srs if s <= 0) / n_boot
    return {"sharpe_ci95": [round(lo, 3), round(hi, 3)], "p_sharpe_le_0": round(p_neg, 3),
            "block": block, "flag": bool(lo <= 0 or p_neg > 0.05)}


def permutation_test(returns: Sequence[float], n_perm: int = 1000, seed: int = 0) -> dict:
    """Order-structure test: the lag-1 autocorrelation of the equity curve's increments should be no larger than
    a random permutation of the same returns would give. A significant excess flags smoothing / stale marks /
    fabricated equity-curve momentum (Sharpe itself is order-invariant, so this catches what it can't)."""
    n = len(returns)
    if n < 16:
        return {"error": "need >= 16 returns"}
    obs = abs(_autocorr(returns, 1))
    rng = random.Random(seed)
    xs = list(returns)
    ge = 0
    for _ in range(n_perm):
        rng.shuffle(xs)
        if abs(_autocorr(xs, 1)) >= obs:
            ge += 1
    p = (ge + 1) / (n_perm + 1)
    return {"observed_abs_autocorr1": round(obs, 3), "perm_p_value": round(p, 4), "flag": bool(p < 0.05)}


# --------------------------------------------------------------------------- #
# B-tier
# --------------------------------------------------------------------------- #
def cost_capacity(returns: Sequence[float], turnover: Optional[Sequence[float]] = None,
                  turnover_per_period: Optional[float] = None) -> dict:
    """Breakeven-cost stress: the per-unit-turnover cost at which net Sharpe hits zero. A strategy whose
    breakeven cost is a fraction of a basis point is a fill-assumption fantasy."""
    n = len(returns)
    if turnover is not None:
        tot_to = sum(turnover[:n])
    elif turnover_per_period is not None:
        tot_to = turnover_per_period * n
    else:
        return {"error": "need turnover series or turnover_per_period"}
    gross = sum(returns)
    breakeven = gross / tot_to if tot_to > 0 else float("inf")
    avg_to = tot_to / n
    return {"breakeven_cost_per_unit_turnover": round(breakeven, 6),
            "breakeven_cost_bps": round(breakeven * 1e4, 2), "avg_turnover": round(avg_to, 4),
            "flag": bool(breakeven * 1e4 < 5.0)}               # < 5 bps to break even = fragile to costs


def conditional_hetero(returns: Sequence[float], lags: int = 10) -> dict:
    """Volatility-clustering / GARCH check via Ljung–Box on SQUARED returns. Significant → the Sharpe
    denominator is inflated by quiet stretches and real risk clusters elsewhere; report a vol-scaled Sharpe."""
    n = len(returns)
    if n < 4 * lags:
        return {"error": f"need >= {4 * lags} returns"}
    m = _mean(returns)
    sq = [(r - m) ** 2 for r in returns]
    h = min(lags, n - 1)
    q = n * (n + 2) * sum((_autocorr(sq, k) ** 2) / (n - k) for k in range(1, h + 1))
    # chi-square(h) survival via Wilson–Hilferty normal approx
    z = ((q / h) ** (1.0 / 3.0) - (1 - 2.0 / (9 * h))) / math.sqrt(2.0 / (9 * h))
    p = 1 - _norm_cdf(z)
    return {"ljung_box_q_squared": round(q, 2), "lags": h, "p_value": round(p, 4),
            "flag": bool(p < 0.05)}


def bh_fdr(p_values: Sequence[float], q: float = 0.05) -> dict:
    """Benjamini–Hochberg false-discovery control — for when many strategies/metrics were tested and you want
    the significance threshold that keeps the expected false-discovery rate <= q."""
    m = len(p_values)
    if m == 0:
        return {"error": "need p-values"}
    order = sorted(range(m), key=lambda i: p_values[i])
    thresh, k = 0.0, 0
    for rank, i in enumerate(order, start=1):
        if p_values[i] <= (rank / m) * q:
            thresh, k = p_values[i], rank
    return {"bh_threshold": round(thresh, 6), "n_significant": k, "n_tested": m,
            "flag": bool(k == 0)}


# --------------------------------------------------------------------------- #
# orchestrator
# --------------------------------------------------------------------------- #
def run_battery(returns: Sequence[float], *, positions: Optional[Sequence[float]] = None,
                asset_returns: Optional[Sequence[float]] = None, turnover: Optional[Sequence[float]] = None,
                candidates: Optional[Sequence[Sequence[float]]] = None,
                periods_per_year: int = 252) -> dict:
    """Run every applicable check and return a combined verdict. A check runs only if its inputs are present.
    `survives` is True only if NO check flags. `flags` names what tripped."""
    checks: dict = {}

    def _add(name, res):
        if isinstance(res, dict) and "error" not in res:
            checks[name] = res

    _add("hac_sharpe", hac_sharpe(returns))
    _add("regime_stability", regime_stability(returns))
    _add("drawdown", drawdown_stats(returns, periods_per_year))
    _add("bootstrap_stability", bootstrap_stability(returns))
    _add("permutation", permutation_test(returns))
    _add("conditional_hetero", conditional_hetero(returns))
    if positions is not None and asset_returns is not None:
        _add("leakage", leakage_scan(positions, asset_returns))
    if turnover is not None:
        _add("cost_capacity", cost_capacity(returns, turnover=turnover))
    if candidates is not None:
        _add("pbo", pbo(candidates))

    flags = [name for name, r in checks.items() if r.get("flag")]
    survives = len(flags) == 0
    # severity: look-ahead and overfitting make the backtest fiction (critical); inflated/fragile Sharpe is
    # high; tail/cost/vol issues are medium. The worst flagged check sets the level.
    _SEV = {"leakage": "critical", "pbo": "critical",
            "hac_sharpe": "high", "regime_stability": "high", "bootstrap_stability": "high",
            "drawdown": "medium", "permutation": "medium", "conditional_hetero": "medium",
            "cost_capacity": "medium"}
    _ORDER = {"critical": 3, "high": 2, "medium": 1}
    risk = "none" if survives else max((_SEV.get(f, "medium") for f in flags), key=lambda s: _ORDER[s])
    verdict = ("PASS — no integrity red flag on this battery (still not a promise of future return)."
               if survives else
               f"{risk.upper()} — flagged by {len(flags)} check(s): {', '.join(flags)}. "
               f"Treat the headline Sharpe with suspicion.")
    return {"kind": "backtest_battery", "survives": survives, "risk": risk, "n_checks": len(checks),
            "flags": flags, "checks": checks, "verdict": verdict}


def _selftest():
    rng = random.Random(0)
    # a plausible clean series
    good = [rng.gauss(0.0006, 0.01) for _ in range(750)]
    r = run_battery(good)
    assert "hac_sharpe" in r["checks"] and "drawdown" in r["checks"]
    # an autocorrelated (smoothed) series must trip HAC or permutation
    ac = [0.0] * 750
    prev = 0.0
    for t in range(750):
        prev = 0.6 * prev + rng.gauss(0.0006, 0.01)
        ac[t] = prev
    rb = run_battery(ac)
    assert rb["checks"]["hac_sharpe"]["inflation"] > 1.0
    # leakage: position == sign of same-bar return (perfect look-ahead) must flag
    a = [rng.gauss(0, 0.01) for _ in range(300)]
    pos = [1.0 if x > 0 else -1.0 for x in a]
    lk = leakage_scan(pos, a)
    assert lk["flag"] is True, lk
    print(f"battery selftest: OK ({r['n_checks']} checks; clean={r['survives']}, "
          f"smoothed inflation={rb['checks']['hac_sharpe']['inflation']}, leakage caught)")


if __name__ == "__main__":
    _selftest()
