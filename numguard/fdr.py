"""fdr — a data-driven t-hurdle from your OWN search history (Harvey & Liu 2020), dependency-free.

The Deflated Sharpe Ratio (`numguard.backtest`) answers: does the best of my N trials beat the luckiest
zero-skill trial? Harvey & Liu, "False (and Missed) Discoveries in Financial Economics" (Journal of
Finance, 2020), go one step further: there is no universal hurdle like t > 3. The right hurdle depends on
the multiplicity YOU actually faced and the false-discovery rate YOU are willing to accept — and it can be
estimated from your own trials by bootstrap.

Backtesting frameworks are one of the few places where that multiplicity is OBSERVED rather than guessed:
an optimizer logs every trial it ran. Feed the full panel of trial returns (not just the winner) to
`fdr_hurdle` and get back the t-stat hurdle implied by your target FDR.

Method (single-bootstrap core of Harvey & Liu 2020):
  1. compute each trial's t-stat on the real panel;
  2. demean every trial's returns — the null "no skill anywhere" is now true by construction;
  3. bootstrap time indices (the SAME indices for every trial, preserving cross-trial correlation) and
     recompute t-stats -> the empirical null distribution of the whole panel;
  4. for a candidate hurdle h: estimated FDR(h) = E[# null trials >= h] / max(1, # real trials >= h);
  5. the hurdle is the smallest h whose FDR estimate (and that of every stricter h) is <= the target.

Honest simplifications vs the paper: this treats ALL trials as null when counting expected false
discoveries (conservative, like Benjamini-Hochberg with m0 = m), and the optional outer bootstrap
(`n_outer`) reports sampling variability of the hurdle rather than the paper's full double-bootstrap
p-value calibration. Type II (missed discoveries) is reported descriptively, not optimized.

Pure `math` + seeded `random` — no numpy/scipy — so an agent can call it anywhere, deterministically.
"""
from __future__ import annotations
import math
import random
from bisect import bisect_left
from dataclasses import dataclass, field

from .backtest import _norm_cdf


# --------------------------------------------------------------------------- #
# panel helpers
# --------------------------------------------------------------------------- #
def _check_panel(panel):
    if not panel:
        raise ValueError("empty panel: pass one return series per trial (an empty measurement is not a pass)")
    T = len(panel[0])
    if T < 8:
        raise ValueError(f"need at least 8 periods per trial, got T={T}")
    for i, series in enumerate(panel):
        if len(series) != T:
            raise ValueError(f"trial {i} has {len(series)} periods, trial 0 has {T}: panel must be time-aligned")
    return T


def t_stat(series) -> float:
    """Plain t-statistic of the mean: mean / (sd / sqrt(T)), sample sd (ddof=1)."""
    T = len(series)
    mu = sum(series) / T
    var = sum((x - mu) ** 2 for x in series) / (T - 1)
    if var <= 0:
        return 0.0
    return mu / math.sqrt(var / T)


def _resampled_t(values, squares, idx, T):
    s1 = 0.0
    s2 = 0.0
    for j in idx:
        s1 += values[j]
        s2 += squares[j]
    mu = s1 / T
    var = (s2 - T * mu * mu) / (T - 1)
    if var <= 1e-18:
        return 0.0
    return mu / math.sqrt(var / T)


# --------------------------------------------------------------------------- #
# the verdict
# --------------------------------------------------------------------------- #
@dataclass
class FdrHurdleVerdict:
    m: int                      # number of trials in the panel
    T: int                      # periods per trial
    target_fdr: float
    hurdle: float               # smallest |t| hurdle whose estimated FDR <= target (conservatively)
    t_stats: list               # observed t-stat per trial
    discoveries: list           # indices of trials with |t| >= hurdle
    fdr_at_hurdle: float        # estimated FDR exactly at the hurdle
    expected_false_at_hurdle: float  # E[# null trials clearing the hurdle] per bootstrap draw
    grid: list = field(repr=False)          # candidate hurdles
    fdr_curve: list = field(repr=False)     # estimated FDR at each grid point
    n_boot: int = 0
    seed: int = 0
    hurdle_ci: tuple = None     # (lo, hi) percentile CI from the outer bootstrap, if n_outer > 0
    note: str = ""

    def __str__(self) -> str:
        d = f"{len(self.discoveries)}/{self.m} trials clear it" if self.discoveries else \
            f"NO trial clears it (best |t|={max(abs(t) for t in self.t_stats):.2f})"
        ci = f", hurdle CI {self.hurdle_ci[0]:.2f}-{self.hurdle_ci[1]:.2f}" if self.hurdle_ci else ""
        return (f"data-driven hurdle |t|>={self.hurdle:.2f} at target FDR {self.target_fdr:.0%} "
                f"(m={self.m} trials, T={self.T}); {d}; "
                f"E[false discoveries]={self.expected_false_at_hurdle:.2f}{ci}")


def fdr_hurdle(panel, target_fdr: float = 0.05, n_boot: int = 1000, seed: int = 0,
               grid_step: float = 0.05, n_outer: int = 0) -> FdrHurdleVerdict:
    """The t-stat hurdle YOUR search history implies at YOUR false-discovery-rate target.

    `panel` = one return series per trial you actually ran (time-aligned lists) — the winner alone is not
    enough; the whole point is that the hurdle comes from the multiplicity you really faced.
    `target_fdr` = the share of your discoveries you are willing to have be false (Harvey & Liu use 5%).
    `n_outer` > 0 additionally bootstraps the whole procedure to put a percentile CI on the hurdle
    (costly: n_outer full re-runs at n_boot//4 inner draws each).
    """
    if not 0.0 < target_fdr < 1.0:
        raise ValueError("need 0 < target_fdr < 1")
    if n_boot < 100:
        raise ValueError("n_boot < 100 would make the null too coarse to trust")
    T = _check_panel(panel)
    m = len(panel)

    obs = [t_stat(s) for s in panel]
    hurdle, curve, grid, ev_at = _hurdle_from_panel(panel, obs, T, m, target_fdr, n_boot, seed, grid_step)
    abs_obs = sorted(abs(t) for t in obs)
    R = m - bisect_left(abs_obs, hurdle)
    discoveries = [i for i, t in enumerate(obs) if abs(t) >= hurdle]
    fdr_here = min(1.0, ev_at / R) if R else 0.0

    ci = None
    if n_outer:
        rng = random.Random(seed + 1_000_003)
        inner = max(200, n_boot // 4)
        hs = []
        for _ in range(n_outer):
            idx = [rng.randrange(T) for _ in range(T)]
            re_panel = [[s[j] for j in idx] for s in panel]
            re_obs = [t_stat(s) for s in re_panel]
            h, *_ = _hurdle_from_panel(re_panel, re_obs, T, m, target_fdr, inner,
                                       rng.randrange(2**30), grid_step)
            hs.append(h)
        hs.sort()
        ci = (hs[int(0.05 * len(hs))], hs[min(len(hs) - 1, int(0.95 * len(hs)))])

    note = "" if discoveries else "no discovery at this FDR target — the panel's best trial is within noise"
    return FdrHurdleVerdict(m, T, target_fdr, hurdle, obs, discoveries, fdr_here, ev_at,
                            grid, curve, n_boot, seed, ci, note)


def _hurdle_from_panel(panel, obs, T, m, target_fdr, n_boot, seed, grid_step):
    """Core: bootstrap the demeaned panel -> null |t| pool -> conservative smallest passing hurdle."""
    rng = random.Random(seed)
    demeaned, squares = [], []
    for s in panel:
        mu = sum(s) / T
        d = [x - mu for x in s]
        demeaned.append(d)
        squares.append([x * x for x in d])

    null_abs = []
    for _ in range(n_boot):
        idx = [rng.randrange(T) for _ in range(T)]     # same indices for every trial: keeps correlation
        for d, sq in zip(demeaned, squares):
            null_abs.append(abs(_resampled_t(d, sq, idx, T)))
    null_abs.sort()
    n_null = len(null_abs)

    abs_obs = sorted(abs(t) for t in obs)
    top = max(4.0, abs_obs[-1] + 0.5)
    grid = [round(k * grid_step, 10) for k in range(int(top / grid_step) + 2)]

    curve = []
    for h in grid:
        ev = (n_null - bisect_left(null_abs, h)) / n_boot   # E[# null trials >= h]
        R = m - bisect_left(abs_obs, h)                     # observed discoveries at h
        curve.append(min(1.0, ev / R) if R else 0.0)

    # conservative pick: smallest h such that EVERY stricter hurdle also meets the target
    hurdle = grid[-1]
    tail_max = 0.0
    for h, f in zip(reversed(grid), reversed(curve)):
        tail_max = max(tail_max, f)
        if tail_max <= target_fdr:
            hurdle = h
    ev_at = (n_null - bisect_left(null_abs, hurdle)) / n_boot
    return hurdle, curve, grid, ev_at


# --------------------------------------------------------------------------- #
# selftest — analytic rung + planted signal + null control + determinism
# --------------------------------------------------------------------------- #
def _selftest():
    rng = random.Random(42)

    # analytic rung (independent of the bootstrap code path): for independent ~N(0,1) trials,
    # E[# >= h] = m * 2*(1 - Phi(h)). Check the bootstrap null pool against it at h=2.
    m, T = 40, 300
    null_panel = [[rng.gauss(0, 1) for _ in range(T)] for _ in range(m)]
    v = fdr_hurdle(null_panel, target_fdr=0.10, n_boot=400, seed=7)
    h = 2.0
    analytic = m * 2 * (1 - _norm_cdf(h))
    pool = _null_pool(null_panel, T, 400, 7)
    ev = sum(1 for x in pool if x >= h) / 400
    assert 0.4 * analytic < ev < 2.0 * analytic, (ev, analytic)

    # null control: an all-noise panel should yield few/no discoveries at FDR 10%
    assert len(v.discoveries) <= max(1, int(0.10 * m) + 1), v

    # planted signal: 4 trials with real drift (t ~= 4) must be found, nulls must not flood in
    strong = [[rng.gauss(4.0 / math.sqrt(T), 1) for _ in range(T)] for _ in range(4)]
    v2 = fdr_hurdle(null_panel + strong, target_fdr=0.10, n_boot=400, seed=7)
    planted = set(range(m, m + 4))
    found_planted = planted & set(v2.discoveries)
    false_in = set(v2.discoveries) - planted
    assert len(found_planted) >= 3, v2
    assert len(false_in) <= 2, v2
    assert 1.0 < v2.hurdle < 4.5, v2

    # determinism: same seed -> identical hurdle and discoveries
    v3 = fdr_hurdle(null_panel + strong, target_fdr=0.10, n_boot=400, seed=7)
    assert v3.hurdle == v2.hurdle and v3.discoveries == v2.discoveries

    # guards: empty panel / ragged panel / tiny T all refuse loudly
    for bad in ([], [[1, 2, 3]], [[0.1] * 50, [0.1] * 49]):
        try:
            fdr_hurdle(bad)
            raise AssertionError(f"accepted bad panel {bad!r}")
        except ValueError:
            pass

    print("fdr selftest: OK (analytic E[V] rung, null control, planted signal, determinism, guards)")


def _null_pool(panel, T, n_boot, seed):
    """Rebuild the null |t| pool exactly as _hurdle_from_panel does (for the analytic rung)."""
    rng = random.Random(seed)
    demeaned = [[x - sum(s) / T for x in s] for s in panel]
    squares = [[x * x for x in d] for d in demeaned]
    pool = []
    for _ in range(n_boot):
        idx = [rng.randrange(T) for _ in range(T)]
        for d, sq in zip(demeaned, squares):
            pool.append(abs(_resampled_t(d, sq, idx, T)))
    return pool


if __name__ == "__main__":
    _selftest()
