"""fdr — the data-driven t-hurdle (Harvey & Liu 2020) must earn its verdicts, not assert them."""
import math
import random

import pytest

from numguard import fdr_hurdle
from numguard.fdr import _null_pool, t_stat
from numguard.backtest import _norm_cdf


def _panel(m, T, drift, seed):
    rng = random.Random(seed)
    return [[rng.gauss(drift, 1) for _ in range(T)] for _ in range(m)]


# ---- analytic rung: known value computed WITHOUT the bootstrap code path ----
def test_null_pool_matches_analytic_exceedance():
    m, T = 40, 300
    panel = _panel(m, T, 0.0, 42)
    pool = _null_pool(panel, T, 400, 7)
    ev = sum(1 for x in pool if x >= 2.0) / 400
    analytic = m * 2 * (1 - _norm_cdf(2.0))   # independent-normal identity
    assert 0.4 * analytic < ev < 2.0 * analytic


# ---- null control: an all-noise search history must yield ~no discoveries ----
def test_null_panel_yields_no_flood_of_discoveries():
    v = fdr_hurdle(_panel(40, 300, 0.0, 42), target_fdr=0.10, n_boot=400, seed=7)
    assert len(v.discoveries) <= 2
    if not v.discoveries:
        assert "within noise" in v.note


# ---- planted signal: real drift must clear the hurdle, noise must not ride along ----
def test_planted_signal_is_discovered():
    T = 300
    nulls = _panel(40, T, 0.0, 42)
    strong = _panel(4, T, 4.0 / math.sqrt(T), 99)
    v = fdr_hurdle(nulls + strong, target_fdr=0.10, n_boot=400, seed=7)
    planted = set(range(40, 44))
    assert len(planted & set(v.discoveries)) >= 3
    assert len(set(v.discoveries) - planted) <= 2
    assert 1.0 < v.hurdle < 4.5


# ---- determinism: same seed -> identical verdict (parity with itself) ----
def test_deterministic_given_seed():
    panel = _panel(10, 100, 0.0, 5)
    a = fdr_hurdle(panel, n_boot=200, seed=3)
    b = fdr_hurdle(panel, n_boot=200, seed=3)
    assert a.hurdle == b.hurdle and a.discoveries == b.discoveries
    assert a.fdr_curve == b.fdr_curve


def test_outer_bootstrap_ci_brackets_hurdle():
    T = 200
    panel = _panel(20, T, 0.0, 1) + _panel(2, T, 3.5 / math.sqrt(T), 2)
    v = fdr_hurdle(panel, target_fdr=0.10, n_boot=300, seed=3, n_outer=20)
    lo, hi = v.hurdle_ci
    assert lo <= hi
    assert lo <= v.hurdle + 0.5 and hi >= v.hurdle - 0.5


# ---- guards: refuse loudly instead of passing silently ----
@pytest.mark.parametrize("bad", [[], [[1.0, 2.0, 3.0]], [[0.1] * 50, [0.1] * 49]])
def test_bad_panels_raise(bad):
    with pytest.raises(ValueError):
        fdr_hurdle(bad)


def test_bad_params_raise():
    panel = _panel(3, 50, 0.0, 1)
    with pytest.raises(ValueError):
        fdr_hurdle(panel, target_fdr=0.0)
    with pytest.raises(ValueError):
        fdr_hurdle(panel, n_boot=50)


def test_t_stat_zero_variance_is_zero_not_crash():
    assert t_stat([0.5] * 20) == 0.0


# =========================================================================== #
# the double bootstrap of Harvey & Liu (2020), Steps I-IV
# =========================================================================== #
from numguard.fdr import double_bootstrap_errors, harvey_liu_hurdle, hurdle_curve

_KW = dict(n_outer=15, n_inner=15, seed=3)


def _mixed_panel():
    """16 pure-noise strategies + 4 genuinely skilled ones (true t ~ 4)."""
    T = 120
    return _panel(16, T, 0.0, 11) + _panel(4, T, 4.0 / math.sqrt(T), 12)


# ---- analytic rung 1: at cutoff 0 the contingency table is fixed by construction ----
def test_cutoff_zero_error_rates_are_analytic():
    """Declare everything significant: TP=n_alt, FP=m-n_alt, FN=TN=0, whatever the data did.

    So RFDR = (m-n_alt)/m exactly, RMISS = 0 (empty denominator) and RRATIO = 0 (FN=0).
    This value never passes through the bootstrap, so it catches a resampler that is
    silently doing nothing as well as one that is doing the wrong thing.
    """
    cs, t1, t2, orr, m, T, n_alt = double_bootstrap_errors(_mixed_panel(), 0.20, **_KW)
    assert cs[0] == 0.0 and n_alt == 4 and m == 20
    assert t1[0] == pytest.approx((m - n_alt) / m, abs=1e-12)
    assert t2[0] == 0.0
    assert orr[0] == 0.0


# ---- analytic rung 2: at an unreachable cutoff the table is fixed the other way ----
def test_unreachable_cutoff_error_rates_are_analytic():
    """Declare nothing significant: FP=TP=0, FN=n_alt, TN=m-n_alt exactly."""
    _, t1, t2, orr, m, _, n_alt = double_bootstrap_errors(
        _mixed_panel(), 0.20, cutoffs=[99.0], **_KW)
    assert t1[0] == 0.0 and orr[0] == 0.0          # paper's convention when FP+TP = 0
    assert t2[0] == pytest.approx(n_alt / m, abs=1e-12)


# ---- the two error rates must move in opposite directions ----
def test_type1_falls_and_type2_rises_with_the_cutoff():
    _, t1, t2, _, _, _, _ = double_bootstrap_errors(_mixed_panel(), 0.20, **_KW)
    assert t1[0] > t1[-1]      # stricter -> fewer false discoveries
    assert t2[0] < t2[-1]      # stricter -> more misses


# ---- the hurdle cannot rise when more strategies are assumed to be real ----
def test_hurdle_is_monotone_in_assumed_p0():
    """More true alternatives means more true positives at any cutoff, so the FDR target
    is met sooner. At p0=0 no true positive is even possible, RFDR is 1 whenever any null
    clears, and TYPE1 degenerates to the FAMILY-WISE error rate — the strictest case."""
    panel = _mixed_panel()
    hs = [harvey_liu_hurdle(panel, p0, 0.05, **_KW).hurdle
          for p0 in (0.0, 0.05, 0.10, 0.20, 0.40)]
    assert hs == sorted(hs, reverse=True), hs


def test_p0_zero_is_flagged_as_degenerate_not_reported_as_a_type2_of_zero():
    v = harvey_liu_hurdle(_mixed_panel(), 0.0, 0.05, **_KW)
    assert v.n_alt == 0 and v.type2_at == 0.0
    assert "degenerate" in v.note      # a 0.000 miss rate here means "no alternatives exist"


def test_cheap_run_admits_it_is_cheap():
    v = harvey_liu_hurdle(_mixed_panel(), 0.20, 0.05, **_KW)
    assert "225 simulations" in v.note and "100x100" in v.note


def test_oratio_criterion_prices_the_two_errors_against_each_other():
    """Target ORATIO = 1/k when a false discovery costs k times a miss (the paper's example).
    A costlier false discovery must buy a stricter hurdle."""
    panel = _mixed_panel()
    hs = [harvey_liu_hurdle(panel, 0.20, 1.0 / k, criterion="oratio", **_KW).hurdle
          for k in (1, 3, 10)]
    assert hs == sorted(hs), hs
    assert hs[0] < hs[-1]      # equal cost is genuinely more permissive than 10:1


def test_double_bootstrap_is_deterministic_given_seed():
    panel = _mixed_panel()
    a = harvey_liu_hurdle(panel, 0.20, 0.05, **_KW)
    b = harvey_liu_hurdle(panel, 0.20, 0.05, **_KW)
    assert (a.hurdle, a.type1_at, a.type2_at, a.oratio_at) == \
           (b.hurdle, b.type1_at, b.type2_at, b.oratio_at)
    assert a.type2 == b.type2


def test_hurdle_curve_reports_across_p0_rather_than_hiding_the_assumption():
    rows = hurdle_curve(_mixed_panel(), target=0.05, n_outer=8, n_inner=8, seed=3)
    assert [p for p, _ in rows] == [0.0, 0.005, 0.02, 0.05, 0.10, 0.15, 0.20]
    assert all(isinstance(v.hurdle, float) for _, v in rows)


@pytest.mark.parametrize("bad_p0", [-0.01, 1.0, 1.5])
def test_bad_p0_raises(bad_p0):
    with pytest.raises(ValueError):
        harvey_liu_hurdle(_mixed_panel(), bad_p0, **_KW)


def test_bad_criterion_raises():
    with pytest.raises(ValueError):
        harvey_liu_hurdle(_mixed_panel(), 0.1, criterion="nonsense", **_KW)
