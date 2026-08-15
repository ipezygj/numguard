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

`fdr_hurdle` is the single-bootstrap core: it treats ALL trials as null when counting expected false
discoveries (conservative, like Benjamini-Hochberg with m0 = m), and its optional `n_outer` reports
sampling variability of the hurdle. Because it never represents the alternative, it cannot say anything
about MISSED discoveries — which is half of the paper's title.

`harvey_liu_hurdle` in the second half of this module is the paper's actual double bootstrap (Steps I-IV),
which builds a pseudo-population with a known truth and so reports Type I, Type II (false omission rate)
and ORATIO together. It costs I*J panel resamples; `fdr_hurdle` costs n_boot. Use the cheap one to screen,
the faithful one to report.

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
# the double bootstrap of Harvey & Liu (2020), Steps I-IV
# --------------------------------------------------------------------------- #
#
#   Step I.   Bootstrap the time periods; let the bootstrapped panel be X_i and its
#             1 x N vector of t-statistics be t_i.
#   Step II.  Rank t_i. For the top p0 of strategies, take the corresponding series in
#             the ORIGINAL data X_0 and shift them to have the same means as those
#             strategies have in X_i. Shift every remaining series to zero mean. Call
#             the result Y_i. In Y_i we KNOW which strategies are alternatives (the top
#             p0) and which are nulls (the rest) — that is what makes misses countable.
#   Step III. Bootstrap Y_i J times; at each cutoff count TP/FP/TN/FN against that known
#             truth and compute the realised error rates.
#   Step IV.  Repeat I times; average over I*J.
#
# Why Step II takes the means from X_i and not from X_0: the top of X_0 is the winner of
# a search, so its in-sample mean is inflated by the selection itself. Ranking on a
# bootstrap draw and using that draw's means is the paper's way of putting a less
# selection-biased effect size into the alternative.
#
# Error rates, as defined in the paper's Section I:
#   RFDR   = FP/(FP+TP) if FP+TP > 0 else 0   -> averaged over I*J: TYPE1
#   RMISS  = FN/(FN+TN) if FN+TN > 0 else 0   -> averaged over I*J: TYPE2
#   RRATIO = FP/FN      if FN    > 0 else 0   -> averaged over I*J: ORATIO
#
# TYPE2 is the FALSE OMISSION RATE — misses as a share of everything you declared
# insignificant — deliberately the mirror of FDR, not the textbook 1-power.
#
# ORATIO is the odds of a false discovery per miss, and it is the number to target when
# the two errors cost different amounts. The paper's own example: "if an investor
# believes that the cost of a Type I error is 10 times that of a Type II error, then the
# optimal ORATIO should be 1/10."
#
# p0 is NOT estimated here, because the paper does not estimate it either: it conditions
# on p0 and reports the answer across plausible values (their applications use 0, 0.5%,
# 2%, 5%, 10%, 15%, 20%). Hence p0 is a required argument and `hurdle_curve` exists.
#
# Paper's parameters: I = 100, J = 100 (10,000 simulations).


@dataclass
class DoubleBootstrapVerdict:
    m: int
    T: int
    p0: float                   # ASSUMED fraction of strategies that are truly non-null (an input)
    n_alt: int                  # round(p0 * m): how many are treated as alternatives
    hurdle: float               # smallest cutoff meeting the target
    target: float
    criterion: str              # "fdr" (target TYPE1) or "oratio" (target ORATIO)
    type1_at: float             # TYPE1 at the hurdle
    type2_at: float             # TYPE2 at the hurdle
    oratio_at: float            # ORATIO at the hurdle
    cutoffs: list = field(repr=False)
    type1: list = field(repr=False)
    type2: list = field(repr=False)
    oratio: list = field(repr=False)
    n_outer: int = 0
    n_inner: int = 0
    seed: int = 0
    note: str = ""

    def __str__(self) -> str:
        what = "TYPE1" if self.criterion == "fdr" else "ORATIO"
        return (f"Harvey-Liu double bootstrap: |t| >= {self.hurdle:.2f} at target {what} "
                f"{self.target:.3g} (assumed p0={self.p0:.1%} -> {self.n_alt}/{self.m} alternatives, "
                f"T={self.T}); TYPE1={self.type1_at:.3f} TYPE2={self.type2_at:.3f} "
                f"ORATIO={self.oratio_at:.2f}" + (f" | {self.note}" if self.note else ""))


def double_bootstrap_errors(panel, p0: float, cutoffs=None, n_outer: int = 100,
                            n_inner: int = 100, seed: int = 0):
    """TYPE1, TYPE2 and ORATIO at every cutoff, by Steps I-IV above.

    Returns (cutoffs, type1, type2, oratio, m, T, n_alt).

    `p0` is the ASSUMED fraction of genuinely non-null strategies — an input, not an
    estimate. Cost is n_outer * n_inner resamples of the whole panel, so the paper's
    100 x 100 takes real time in pure Python; if you lower them, say so when you report
    the number (the verdict's `note` does this for you).
    """
    if not 0.0 <= p0 < 1.0:
        raise ValueError("need 0 <= p0 < 1 (p0 is the assumed non-null fraction, not a p-value)")
    if n_outer < 1 or n_inner < 1:
        raise ValueError("n_outer and n_inner must both be >= 1")
    T = _check_panel(panel)
    m = len(panel)
    n_alt = int(round(p0 * m))
    if cutoffs is None:
        cutoffs = [round(0.05 * k, 10) for k in range(121)]      # 0.00 .. 6.00
    cutoffs = list(cutoffs)
    n_c = len(cutoffs)

    rng = random.Random(seed)
    centred = [[x - sum(s) / T for x in s] for s in panel]
    raw_sq = [[x * x for x in s] for s in panel]

    sum1 = [0.0] * n_c
    sum2 = [0.0] * n_c
    sumr = [0.0] * n_c
    draws = 0

    for _ in range(n_outer):
        # ---- Step I ----------------------------------------------------------
        idx = [rng.randrange(T) for _ in range(T)]
        t_i, boot_mu = [], []
        for s, sq in zip(panel, raw_sq):
            s1 = s2 = 0.0
            for j in idx:
                s1 += s[j]
                s2 += sq[j]
            mu = s1 / T
            var = (s2 - T * mu * mu) / (T - 1)
            boot_mu.append(mu)
            t_i.append(0.0 if var <= 1e-18 else mu / math.sqrt(var / T))

        # ---- Step II: top p0 by t_i become the alternatives, with X_i's means ----
        order = sorted(range(m), key=lambda k: t_i[k], reverse=True)
        is_alt = [False] * m
        for k in order[:n_alt]:
            is_alt[k] = True
        y_vals, y_sq = [], []
        for k in range(m):
            shift = boot_mu[k] if is_alt[k] else 0.0
            row = [x + shift for x in centred[k]] if shift else centred[k]
            y_vals.append(row)
            y_sq.append([x * x for x in row])

        # ---- Step III: resample Y_i, count against the known truth ------------
        for _ in range(n_inner):
            jdx = [rng.randrange(T) for _ in range(T)]
            all_abs, alt_abs = [], []
            for k in range(m):
                a = abs(_resampled_t(y_vals[k], y_sq[k], jdx, T))
                all_abs.append(a)
                if is_alt[k]:
                    alt_abs.append(a)
            all_abs.sort()
            alt_abs.sort()
            for ci, c in enumerate(cutoffs):
                # counting by bisect, not by rescanning m strategies per cutoff
                n_sig = m - bisect_left(all_abs, c)
                tp = n_alt - bisect_left(alt_abs, c)
                fp = n_sig - tp
                fn = n_alt - tp
                tn = m - n_alt - fp
                if fp + tp > 0:
                    sum1[ci] += fp / (fp + tp)
                if fn + tn > 0:
                    sum2[ci] += fn / (fn + tn)
                if fn > 0:
                    sumr[ci] += fp / fn
            draws += 1

    d = float(draws)
    return (cutoffs, [x / d for x in sum1], [x / d for x in sum2], [x / d for x in sumr],
            m, T, n_alt)


def harvey_liu_hurdle(panel, p0: float, target: float = 0.05, criterion: str = "fdr",
                      cutoffs=None, n_outer: int = 100, n_inner: int = 100,
                      seed: int = 0) -> DoubleBootstrapVerdict:
    """The t-hurdle of Harvey & Liu (2020) at a target Type I rate, or at a target odds ratio.

    criterion="fdr"    -> smallest cutoff whose TYPE1 <= target.
    criterion="oratio" -> smallest cutoff whose ORATIO <= target. Read that target as the
                          INVERSE cost ratio: if a false discovery costs ten times a miss,
                          the paper's own example puts the target at 1/10.

    `p0` is assumed, not estimated — see `hurdle_curve` to report across plausible values,
    which is what the paper does and what any honest single number here has to admit to.
    """
    if criterion not in ("fdr", "oratio"):
        raise ValueError("criterion must be 'fdr' or 'oratio'")
    if target <= 0:
        raise ValueError("target must be > 0")
    cs, t1, t2, orr, m, T, n_alt = double_bootstrap_errors(
        panel, p0, cutoffs, n_outer, n_inner, seed)
    series = t1 if criterion == "fdr" else orr

    # conservative pick, as in fdr_hurdle: every STRICTER cutoff must also meet the target
    hurdle = cs[-1]
    tail_max = 0.0
    for c, v in zip(reversed(cs), reversed(series)):
        tail_max = max(tail_max, v)
        if tail_max <= target:
            hurdle = c
    i = cs.index(hurdle)

    note = ""
    if n_alt == 0:
        note = ("p0 rounds to 0 alternatives: TYPE2 and ORATIO are degenerate by construction, "
                "only TYPE1 is meaningful here")
    elif n_alt == m:
        note = "p0 rounds to ALL strategies: there are no nulls left, so TYPE1 is 0 by construction"
    elif n_outer * n_inner < 2500:
        note = f"only {n_outer}x{n_inner}={n_outer * n_inner} simulations (the paper uses 100x100)"
    return DoubleBootstrapVerdict(m, T, p0, n_alt, hurdle, target, criterion,
                                  t1[i], t2[i], orr[i], cs, t1, t2, orr,
                                  n_outer, n_inner, seed, note)


def hurdle_curve(panel, p0_grid=(0.0, 0.005, 0.02, 0.05, 0.10, 0.15, 0.20),
                 target: float = 0.05, **kw):
    """The hurdle as a function of the assumed p0 — the paper's own grid as the default.

    Reporting one hurdle hides the assumption that produced it. Reporting the curve makes
    the reader supply their own p0, which is the honest interface.
    """
    return [(p0, harvey_liu_hurdle(panel, p0, target, **kw)) for p0 in p0_grid]


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

    _selftest_double_bootstrap()
    print("fdr selftest: OK (analytic E[V] rung, null control, planted signal, determinism, guards)")


def _selftest_double_bootstrap():
    """Steps I-IV: two analytic rungs that bypass the bootstrap, plus the p0 direction."""
    rng = random.Random(11)
    m, T = 20, 120
    panel = [[rng.gauss(0, 1) for _ in range(T)] for _ in range(m - 4)]
    panel += [[rng.gauss(4.0 / math.sqrt(T), 1) for _ in range(T)] for _ in range(4)]
    kw = dict(n_outer=15, n_inner=15, seed=3)

    cs, t1, t2, orr, mm, TT, n_alt = double_bootstrap_errors(panel, 0.20, **kw)
    assert (mm, TT, n_alt) == (m, T, 4)

    # RUNG 1 (analytic, no bootstrap involved): at cutoff 0 EVERY strategy is declared
    # significant, so TP=n_alt, FP=m-n_alt, FN=TN=0 exactly, whatever the data did.
    #   RFDR = (m-n_alt)/m = 1-p0 ;  RMISS = 0 (empty denominator) ;  RRATIO = 0 (FN=0)
    assert cs[0] == 0.0
    assert abs(t1[0] - (m - n_alt) / m) < 1e-12, (t1[0], (m - n_alt) / m)
    assert t2[0] == 0.0 and orr[0] == 0.0, (t2[0], orr[0])

    # RUNG 2 (analytic): at a cutoff nothing can clear, NOTHING is declared significant,
    # so FP=TP=0 (RFDR=0 by the paper's convention) and FN=n_alt, TN=m-n_alt exactly.
    cs2, u1, u2, u3, *_ = double_bootstrap_errors(panel, 0.20, cutoffs=[99.0], **kw)
    assert u1[0] == 0.0 and u3[0] == 0.0, (u1[0], u3[0])
    assert abs(u2[0] - n_alt / m) < 1e-12, (u2[0], n_alt / m)

    # error rates move in the directions the definitions force
    assert t1[0] > t1[-1], (t1[0], t1[-1])       # stricter cutoff -> fewer false discoveries
    assert t2[0] < t2[-1], (t2[0], t2[-1])       # stricter cutoff -> more misses

    # p0 direction: more true alternatives means more true positives at any cutoff, so the
    # FDR target is met sooner and the hurdle cannot rise. At p0=0 there are no possible
    # true positives at all, so RFDR is 1 whenever any null clears — TYPE1 degenerates to
    # the FAMILY-WISE error rate and the hurdle is correspondingly the strictest.
    h0 = harvey_liu_hurdle(panel, 0.0, 0.05, **kw).hurdle
    h20 = harvey_liu_hurdle(panel, 0.20, 0.05, **kw).hurdle
    assert h0 >= h20, (h0, h20)

    # determinism, and the note that stops a cheap run being quoted as a full one
    a = harvey_liu_hurdle(panel, 0.20, 0.05, **kw)
    b = harvey_liu_hurdle(panel, 0.20, 0.05, **kw)
    assert a.hurdle == b.hurdle and a.type2_at == b.type2_at
    assert "15x15" in a.note, a.note
    assert "p0 rounds to 0" in harvey_liu_hurdle(panel, 0.0, 0.05, **kw).note

    # the odds-ratio criterion is a different question and may pick a different hurdle
    o = harvey_liu_hurdle(panel, 0.20, 0.10, criterion="oratio", **kw)
    assert o.criterion == "oratio" and o.hurdle > 0

    # guards
    for bad_p0 in (-0.01, 1.0, 1.5):
        try:
            harvey_liu_hurdle(panel, bad_p0, **kw)
            raise AssertionError(f"accepted p0={bad_p0}")
        except ValueError:
            pass
    try:
        harvey_liu_hurdle(panel, 0.1, criterion="nonsense", **kw)
        raise AssertionError("accepted a bogus criterion")
    except ValueError:
        pass

    print("  double-bootstrap selftest: OK (2 analytic rungs, error-rate directions, "
          "p0 monotonicity, FWER degeneracy, determinism, guards)")


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


def _normal_quantile(p: float) -> float:
    """Inverse standard normal CDF, by bisection on math.erfc.

    Bisection rather than a rational approximation: it is exact to the tolerance
    asked for, has no magic constants to mistype, and is called once per run. A
    package about not trusting numbers should not ship an unchecked polynomial.
    """
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    lo, hi = -12.0, 12.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if 0.5 * math.erfc(-mid / math.sqrt(2.0)) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return (lo + hi) / 2.0


def _read_panel(path):
    """Read a CSV of returns: one column per trial, one row per period.

    A header row of names is optional; it is detected by the first row failing to
    parse as numbers. Ragged rows are rejected rather than padded, because a short
    column silently becomes a different sample size and the hurdle depends on it.
    """
    import csv as _csv

    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = [r for r in _csv.reader(fh) if any(c.strip() for c in r)]
    if not rows:
        raise SystemExit(f"{path}: no data")

    try:
        [float(c) for c in rows[0]]
        names = [f"col_{j}" for j in range(len(rows[0]))]
        body = rows
    except ValueError:
        names = [c.strip() for c in rows[0]]
        body = rows[1:]
    if not body:
        raise SystemExit(f"{path}: header but no rows")

    width = len(names)
    data = []
    for i, r in enumerate(body, start=2):
        if len(r) != width:
            raise SystemExit(f"{path}: row {i} has {len(r)} fields, expected {width}")
        try:
            data.append([float(c) for c in r])
        except ValueError as exc:
            raise SystemExit(f"{path}: row {i}: {exc}")

    panel = [[data[t][j] for t in range(len(data))] for j in range(width)]
    return names, panel


def _read_truth(path):
    if not path:
        return None
    with open(path, encoding="utf-8") as fh:
        return {ln.strip() for ln in fh
                if ln.strip() and not ln.lstrip().startswith("#")}


def _score(names, t_stats, hurdle, truth):
    """Return (found, missed, false_positives) against a known ground truth."""
    picked = {n for n, t in zip(names, t_stats) if abs(t) >= hurdle}
    return (sorted(picked & truth), sorted(truth - picked), sorted(picked - truth))


def _cli(argv=None):
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m numguard.fdr",
        description="The t-stat hurdle your search history implies, from a CSV of "
                    "returns (one column per strategy you tried).")
    ap.add_argument("csv", nargs="?",
                    help="returns CSV; omit to run the built-in selftest")
    ap.add_argument("--truth", help="file listing the genuinely skilled column "
                                    "names, one per line, to score the answer")
    ap.add_argument("--target", type=float, default=0.05,
                    help="target for the criterion (default 0.05)")
    ap.add_argument("--criterion", default="fdr", choices=("fdr", "oratio"),
                    help="what the target refers to (default fdr)")
    ap.add_argument("--p0", type=float,
                    help="assumed fraction of non-null strategies; omit to print "
                         "the whole curve instead of one number")
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    if not args.csv:
        _selftest()
        return 0

    names, panel = _read_panel(args.csv)
    m, T = len(panel), len(panel[0])
    ts = [t_stat(s) for s in panel]
    truth = _read_truth(args.truth)

    print(f"{m} strategies, {T} periods each")
    ranked = sorted(zip(names, ts), key=lambda p: -abs(p[1]))
    print(f"  |t| range           {abs(ranked[-1][1]):.2f} .. {abs(ranked[0][1]):.2f}")

    # Bonferroni is the comparison everyone already knows, so it is the baseline.
    bonf = _normal_quantile(1.0 - 0.05 / (2 * m))
    print(f"\n  Bonferroni 5%       |t| >= {bonf:.2f}"
          f"   -> {sum(1 for t in ts if abs(t) >= bonf)} discoveries")
    if truth:
        f, mi, fp = _score(names, ts, bonf, truth)
        print(f"                       finds {len(f)}/{len(truth)} skilled, "
              f"{len(fp)} false")

    single = fdr_hurdle(panel, target_fdr=args.target, n_boot=args.n_boot,
                        seed=args.seed)
    # .discoveries is the list of surviving column indices, not a count.
    print(f"\n  FDR hurdle          |t| >= {single.hurdle:.2f}"
          f"   -> {len(single.discoveries)} discoveries   "
          f"(target FDR {args.target:g}, expected false "
          f"{single.expected_false_at_hurdle:.2f})")
    if truth:
        f, mi, fp = _score(names, ts, single.hurdle, truth)
        print(f"                       finds {len(f)}/{len(truth)} skilled, "
              f"{len(fp)} false")

    print(f"\n  Harvey & Liu double bootstrap, criterion {args.criterion} "
          f"<= {args.target:g}")
    grid = [(args.p0, harvey_liu_hurdle(panel, args.p0, args.target,
                                        criterion=args.criterion, seed=args.seed))] \
        if args.p0 is not None else hurdle_curve(panel, target=args.target,
                                                 criterion=args.criterion,
                                                 seed=args.seed)
    head = f"    {'p0':>6}{'hurdle':>9}{'TYPE1':>8}{'TYPE2':>8}{'ORATIO':>9}{'disc':>6}"
    print(head + ("  skilled  false" if truth else ""))
    for p0, v in grid:
        disc = sum(1 for t in ts if abs(t) >= v.hurdle)
        line = (f"    {p0:>6.3f}{v.hurdle:>9.2f}{v.type1_at:>8.3f}"
                f"{v.type2_at:>8.3f}{v.oratio_at:>9.2f}{disc:>6}")
        if truth:
            f, mi, fp = _score(names, ts, v.hurdle, truth)
            line += f"{len(f):>9}/{len(truth)}{len(fp):>7}"
        # With no alternatives in the pseudo-population there is nothing to miss,
        # so any criterion built on misses is satisfied at a hurdle of zero and
        # every strategy "survives". That is a degenerate pass, not a result.
        if v.n_alt == 0:
            line += "   <- degenerate: p0 assumes nothing is real"
        print(line)

    if truth:
        print(f"\n  ground truth: {', '.join(sorted(truth))}")
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_cli())
