"""guard — drop 'verify before you assert' into an agent's loop in one line.

numguard installs as a library, so the checks run LOCALLY (no network, no account): any agent framework can gate
a number before it's trusted or asserted. This is the reflex layer — the thing that makes verification a default
instead of a step someone remembers.

    from numguard.guard import require, check, verify_return, require_backtest

    require("backtest", sr=0.12, T=250, n_trials=100)     # raises NumberNotVerified if it doesn't survive
    v = check("model_gap", n=2000, p1=0.85, p2=0.80)      # -> the full verdict dict, never raises

    @verify_return("backtest", extract=lambda r: {"sr": r["sharpe"], "T": r["T"], "n_trials": r["tried"]})
    def my_backtest(...): ...                              # the return is checked before it leaves the function

Register any of these as a tool in LangChain / CrewAI / an MCP server — they're plain callables. The hosted
service (x402/receipts/on-chain) is for when you want a signed, portable, or metered proof; for a local gate you
need nothing but the import.
"""
from __future__ import annotations
import functools
from typing import Callable, Optional

from . import claims


class NumberNotVerified(Exception):
    """Raised by require()/verify_return when a claim does NOT survive its check. Carries the full verdict."""

    def __init__(self, verdict: dict):
        self.verdict = verdict or {}
        super().__init__(self.verdict.get("verdict") or "the number did not survive verification")


def check(kind: str, **inputs) -> dict:
    """Verify a claim and return the full verdict dict (has a boolean `survives`). Never raises on a failed
    check — inspect the result yourself. `kind`: backtest / backtest_series / forward_check / subset_win /
    model_gap / judge_bias."""
    return claims.verify_claim(kind, **inputs)


def survives(kind: str, **inputs) -> bool:
    """True iff the claim survives its check — the one-liner for an `if` gate in an agent's decision."""
    return bool(claims.verify_claim(kind, **inputs).get("survives"))


def require(kind: str, **inputs) -> dict:
    """Verify and RAISE `NumberNotVerified` if it doesn't survive — fail-fast: don't let the loop assert a number
    that didn't pass. Returns the verdict on success so you can quote the honest figure."""
    v = claims.verify_claim(kind, **inputs)
    if not v.get("survives"):
        raise NumberNotVerified(v)
    return v


def require_backtest(returns, *, positions=None, asset_returns=None, turnover=None,
                     candidates=None, periods_per_year: int = 252) -> dict:
    """Run the FULL integrity battery on a real returns series and raise if anything flags (look-ahead, HAC,
    regime, drawdown, PBO, …). The gate for an agent about to trust or deploy a strategy."""
    from . import backtest_battery
    v = backtest_battery.run_battery(returns, positions=positions, asset_returns=asset_returns,
                                     turnover=turnover, candidates=candidates, periods_per_year=periods_per_year)
    if not v.get("survives"):
        raise NumberNotVerified(v)
    return v


def verify_return(kind: str, extract: Optional[Callable] = None):
    """Decorator: verify the value a function returns before it leaves the function. `extract` maps the return
    value into the claim's inputs (default: the return IS the inputs dict). Raises `NumberNotVerified` on a
    failed check — so a function physically cannot hand back an unverified number."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            out = fn(*args, **kwargs)
            inputs = extract(out) if extract else out
            require(kind, **inputs)
            return out
        return wrapped
    return deco


def _selftest():
    # require raises on a claim that doesn't survive, returns on one that does
    try:
        require("backtest", sr=0.12, T=250, n_trials=100)   # overfit -> should raise
        raise AssertionError("require should have raised")
    except NumberNotVerified as e:
        assert e.verdict["survives"] is False
    assert require("backtest", sr=0.15, T=1000, n_trials=1)["survives"] is True
    assert survives("model_gap", n=2000, p1=0.85, p2=0.80) is True

    @verify_return("backtest", extract=lambda r: {"sr": r["sr"], "T": r["T"], "n_trials": r["n"]})
    def strat():
        return {"sr": 0.12, "T": 250, "n": 100, "label": "overfit"}
    try:
        strat()
        raise AssertionError("verify_return should have raised on an overfit backtest")
    except NumberNotVerified:
        pass
    print("guard selftest: OK (require raises/returns, survives, verify_return gates a function)")


if __name__ == "__main__":
    _selftest()
