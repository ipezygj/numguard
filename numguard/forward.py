"""forward — the accountability oracle: does a backtest's claim survive contact with LIVE returns?

numguard verifies a backtest once (Deflated Sharpe). This closes the loop. An agent COMMITS a claimed Sharpe
(a signed receipt = an on-the-record promise), then later feeds the strategy's REALIZED live returns, and
numguard RECONCILES: did the edge HOLD, DECAY, or BREAK? It turns a receipt from a one-shot check into an
accountable track record — "measured, not believed": the number made a promise, here's whether reality kept it.

The test: under the null that the strategy's true per-period Sharpe equals the claim, the realized Sharpe over
T live periods is ~ N(claimed, SE²) with the Mertens/Lo standard error (non-normal-adjusted). A realized Sharpe
far below the claim (small one-sided p) means the backtest overstated — overfit, data-snooped, or regime-gone.

Pure stdlib. `reconcile(claimed_sr, realized_returns, ...)` is the primitive; receipt-able as claim kind
'forward_check'.
"""
from __future__ import annotations
import math
from typing import Sequence

from .backtest import _norm_cdf, sharpe as _sharpe


def _moments(x: Sequence[float]):
    n = len(x)
    m = sum(x) / n
    s2 = sum((v - m) ** 2 for v in x) / n
    sd = math.sqrt(s2) if s2 > 0 else 0.0
    if sd == 0:
        return m, sd, 0.0, 3.0
    sk = sum(((v - m) / sd) ** 3 for v in x) / n
    ku = sum(((v - m) / sd) ** 4 for v in x) / n
    return m, sd, sk, ku


def _sharpe_se(sr: float, T: int, skew: float, kurt: float) -> float:
    """Mertens/Lo asymptotic standard error of a Sharpe estimate, non-normal-adjusted."""
    var = (1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr) / T
    return math.sqrt(var) if var > 0 else math.sqrt(max(1e-12, (1.0 + 0.5 * sr * sr) / T))


def reconcile(claimed_sr: float, realized_returns: Sequence[float], *,
              periods_per_year: int = 252, alpha: float = 0.05) -> dict:
    """Reconcile a CLAIMED per-period Sharpe against a strategy's REALIZED live returns.

    Verdict:
      HELD    — realized is on track (>= claim, or within half a standard error below it)
      DECAYED — realized is below the claim but not significantly (plausibly noise; watch it)
      BROKEN  — realized is significantly below the claim (p <= alpha): the backtest overstated the edge
    """
    T = len(realized_returns)
    if T < 8:
        return {"error": "need >= 8 realized returns"}
    if claimed_sr is None or claimed_sr <= 0:
        return {"error": "claimed_sr must be a positive per-period Sharpe"}

    realized_sr = _sharpe(realized_returns) or 0.0
    _, _, skew, kurt = _moments(realized_returns)
    se = _sharpe_se(claimed_sr, T, skew, kurt)             # SE of a Sharpe estimate under the claim
    z = (realized_sr - claimed_sr) / se                    # negative => underperforming the claim
    p_below = _norm_cdf(z)                                 # one-sided: P(realized this low or lower | true=claim)

    decay_ratio = realized_sr / claimed_sr                 # fraction of the claimed edge realized
    survived_pct = round(100.0 * max(0.0, realized_sr) / claimed_sr, 1)
    ann = periods_per_year ** 0.5

    if realized_sr >= claimed_sr - 0.5 * se:
        verdict, held = "HELD", True
    elif p_below > alpha:
        verdict, held = "DECAYED", False
    else:
        verdict, held = "BROKEN", False

    line = {
        "HELD":    f"HELD — realized Sharpe {realized_sr*ann:+.2f}/yr vs claimed {claimed_sr*ann:+.2f}/yr over "
                   f"{T} live periods; on track.",
        "DECAYED": f"DECAYED — realized {realized_sr*ann:+.2f}/yr is below the claimed {claimed_sr*ann:+.2f}/yr "
                   f"({survived_pct}% of the edge) but within noise (p={p_below:.2g}); watch it.",
        "BROKEN":  f"BROKEN — realized {realized_sr*ann:+.2f}/yr is significantly below the claimed "
                   f"{claimed_sr*ann:+.2f}/yr (p={p_below:.2g}); the backtest overstated the edge.",
    }[verdict]

    return {"kind": "forward_check", "survives": held, "verdict_label": verdict,
            "claimed_sharpe": round(claimed_sr, 4), "realized_sharpe": round(realized_sr, 4),
            "live_periods": T, "sharpe_se": round(se, 4), "z": round(z, 2), "p_below": round(p_below, 4),
            "decay_ratio": round(decay_ratio, 3), "edge_survived_pct": survived_pct,
            "verdict": line}


def min_periods_to_confirm(claimed_sr: float, realized_sr: float, alpha: float = 0.05) -> dict:
    """How many live periods are needed to call a `realized_sr` decay significant at `alpha`? Useful to tell an
    agent 'you don't have enough live data yet to judge this.'"""
    if claimed_sr <= 0 or realized_sr >= claimed_sr:
        return {"needed_periods": 0, "note": "no decay to confirm"}
    from .backtest import _probit
    z_a = -_probit(alpha)                                   # one-sided critical z
    gap = claimed_sr - realized_sr
    var_unit = 1.0 + 0.5 * claimed_sr * claimed_sr
    T = math.ceil(var_unit * (z_a / gap) ** 2)
    return {"needed_periods": int(T), "note": f"~{int(T)} live periods to confirm this decay at alpha={alpha}"}


def _selftest():
    import random
    rng = random.Random(0)
    claimed = 0.12                                          # per-period Sharpe claimed by a backtest
    # (1) reality holds the claim
    good = [rng.gauss(claimed * 0.01, 0.01) for _ in range(300)]
    rg = reconcile(claimed, good)
    # (2) reality breaks it (edge gone -> ~0 Sharpe live)
    dead = [rng.gauss(0.0, 0.01) for _ in range(300)]
    rb = reconcile(claimed, dead)
    assert rb["verdict_label"] in ("BROKEN", "DECAYED") and rb["realized_sharpe"] < claimed
    assert rb["edge_survived_pct"] < 60
    print(f"forward selftest: OK (held={rg['verdict_label']}, dead={rb['verdict_label']} "
          f"survived={rb['edge_survived_pct']}% p={rb['p_below']})")


if __name__ == "__main__":
    _selftest()
