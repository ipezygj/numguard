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
