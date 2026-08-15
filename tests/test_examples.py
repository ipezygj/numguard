"""The shipped panel must produce the numbers the README quotes.

The README tells a reader arriving from a course that a specific command yields
specific hurdles. Prose drifts from code silently, and a package whose whole
argument is that unchecked numbers should not be trusted cannot make an
unchecked claim on its own front page. These tests pin every figure in that
paragraph to the example that ships with it.
"""
import csv
import os

import pytest

from numguard import fdr_hurdle
from numguard.fdr import _normal_quantile, harvey_liu_hurdle, t_stat

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = os.path.join(HERE, "examples", "returns_50_strategies.csv")
TRUTH = os.path.join(HERE, "examples", "returns_50_strategies.truth.txt")


@pytest.fixture(scope="module")
def panel_and_truth():
    with open(CSV, newline="", encoding="utf-8-sig") as fh:
        rows = [r for r in csv.reader(fh) if any(c.strip() for c in r)]
    names = [c.strip() for c in rows[0]]
    data = [[float(c) for c in r] for r in rows[1:]]
    panel = [[data[t][j] for t in range(len(data))] for j in range(len(names))]
    with open(TRUTH, encoding="utf-8") as fh:
        truth = {ln.strip() for ln in fh
                 if ln.strip() and not ln.lstrip().startswith("#")}
    return names, panel, truth


def _score(names, panel, hurdle, truth):
    picked = {n for n, s in zip(names, panel) if abs(t_stat(s)) >= hurdle}
    return len(picked & truth), len(picked - truth)


def test_panel_shape_and_truth(panel_and_truth):
    names, panel, truth = panel_and_truth
    assert len(panel) == 50, "README says 50 strategies"
    assert all(len(s) == 240 for s in panel), "README says 240 periods"
    assert len(truth) == 3, "README says 3 genuinely skilled"
    assert truth <= set(names), "truth file names a column that does not exist"


def test_normal_quantile_matches_known_values():
    # Independent of anything in this package: standard normal quantiles.
    for p, expected in ((0.975, 1.959964), (0.995, 2.575829),
                        (0.9995, 3.290527)):
        assert _normal_quantile(p) == pytest.approx(expected, abs=1e-5)


def test_bonferroni_finds_one_of_three(panel_and_truth):
    names, panel, truth = panel_and_truth
    hurdle = _normal_quantile(1.0 - 0.05 / (2 * len(panel)))
    assert round(hurdle, 2) == 3.29, "README quotes |t| >= 3.29"
    assert _score(names, panel, hurdle, truth) == (1, 0), \
        "README: Bonferroni finds 1 of the 3, no false positives"


def test_fdr_hurdle_quoted_in_readme(panel_and_truth):
    names, panel, truth = panel_and_truth
    v = fdr_hurdle(panel, target_fdr=0.05, n_boot=1000, seed=0)
    assert round(v.hurdle, 2) == 3.40, "README quickstart quotes |t| >= 3.40"
    assert _score(names, panel, v.hurdle, truth) == (1, 0)


def test_oratio_pricing_recovers_all_three(panel_and_truth):
    names, panel, truth = panel_and_truth
    v = harvey_liu_hurdle(panel, 0.02, 0.1, criterion="oratio", seed=0)
    assert round(v.hurdle, 2) == 2.35, "README quotes |t| >= 2.35 at p0 = 0.02"
    assert _score(names, panel, v.hurdle, truth) == (3, 0), \
        "README: recovers all 3 with no false positives"


def test_the_assumption_changes_the_answer(panel_and_truth):
    """The README's own caveat has to hold too, or it is decoration."""
    names, panel, truth = panel_and_truth
    v = harvey_liu_hurdle(panel, 0.05, 0.1, criterion="oratio", seed=0)
    assert round(v.hurdle, 2) == 2.90, "README quotes 2.90 at p0 = 0.05"
    assert _score(names, panel, v.hurdle, truth) == (1, 0), \
        "README: recovery falls back to 1 of 3"


def test_p0_zero_is_degenerate(panel_and_truth):
    """No alternatives means nothing to miss, so a miss-based criterion is
    satisfied at a hurdle of zero. The CLI labels this; it must stay true."""
    _names, panel, _truth = panel_and_truth
    v = harvey_liu_hurdle(panel, 0.0, 0.1, criterion="oratio", seed=0)
    assert v.n_alt == 0
    assert v.hurdle == 0.0
