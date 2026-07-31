"""The vendored statistics must not drift from the library they were copied from.

`numguard/_evalstats.py` is a deliberate copy of evalgate's checks, so that installing
numguard does not drag in a git dependency. A copy is a liability: the day the two
disagree, one of them is quietly wrong and nothing says which. This test pins them
together on concrete inputs — including the ones behind published claims — so drift
fails a build instead of shipping.

Skipped when evalgate is not installed (it is an optional extra); CI installs it.
"""
import math

import pytest

from numguard import _evalstats as V

evalgate_checks = pytest.importorskip(
    "evalgate.checks", reason="evalgate not installed (optional 'leaderboard' extra)"
)
E = evalgate_checks

TOL = 1e-12


def _close(a: float, b: float) -> bool:
    if math.isnan(a) and math.isnan(b):
        return True
    return abs(a - b) < TOL


@pytest.mark.parametrize("p", [0.5, 0.1, 0.009, 1e-6])
@pytest.mark.parametrize("n", [2, 23, 100])
def test_corrections_agree(p, n):
    assert _close(E.sidak(p, n), V.sidak(p, n))
    assert _close(E.bonferroni(p, n), V.bonferroni(p, n))


@pytest.mark.parametrize("k,n", [(68, 100), (715, 1000), (0, 10), (10, 10), (1, 2)])
def test_binomial_agrees(k, n):
    assert _close(E.binomial_test(k, n), V.binomial_test(k, n))


@pytest.mark.parametrize("n", [18, 50, 200, 1000])
@pytest.mark.parametrize("p_base", [0.5, 0.85])
def test_min_detectable_effect_agrees(n, p_base):
    assert _close(E.min_detectable_effect(n, p_base), V.min_detectable_effect(n, p_base))


@pytest.mark.parametrize("n,p1,p2", [(200, 0.85, 0.83), (1000, 0.9, 0.7), (18, 0.94, 0.83)])
def test_power_check_agrees(n, p1, p2):
    a, b = E.power_check(n, p1, p2), V.power_check(n, p1, p2)
    assert _close(a.p_value, b.p_value)
    assert _close(a.mde, b.mde)
    assert a.significant == b.significant and a.resolvable == b.resolvable


@pytest.mark.parametrize("p", [0.5, 0.05, 0.001, 0.975])
def test_probit_agrees(p):
    assert _close(E._probit(p), V.probit(p))


def test_published_claims_still_reproduce():
    """The numbers that appear in public write-ups, recomputed from both copies.

    A subset win reported at p=0.009 out of 23 subsets does not survive correction;
    a judge preferring one side 68 times in 100 is far from chance. If either copy
    stops reproducing these, a published claim has silently changed.
    """
    for mod in (E, V):
        assert 0.18 < mod.sidak(0.009, 23) < 0.20
        assert mod.binomial_test(68, 100) < 1e-3
