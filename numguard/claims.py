"""verify_claim — the one primitive an agent calls before it asserts a number.

An agent about to say "we lead on subset X (p=0.03)", "model A beats B", "the judge prefers ours 68%", or
"this strategy Sharpes 2.1" routes the claim here first and gets back a structured verdict: does it survive
the check it skipped, and what is the honest number to report instead.

Shared statistics come from `evalgate` (the zero-dep eval-integrity library); backtest claims use numguard's
own Deflated Sharpe. One call, one verdict.
"""
from __future__ import annotations
import sys
from pathlib import Path

try:
    from evalgate import correct_best_of, power_check, bias_rate
except ImportError:                       # local dev: sibling checkout
    sys.path.insert(0, str(Path.home() / "evalgate"))
    from evalgate import correct_best_of, power_check, bias_rate

from .backtest import deflated_sharpe

KINDS = ("subset_win", "model_gap", "judge_bias", "backtest")


def verify_claim(kind: str, **p) -> dict:
    """Route a numeric claim to the check that most directly tests it. Returns a dict with a boolean
    `survives`, the corrected/key numbers, and a plain-English `verdict` line an agent can quote.

    kind="subset_win"  p, n_tests             — a best-of-N subset/metric/checkpoint win
    kind="model_gap"   n, p1, p2              — accuracy gap between two models over n items each
    kind="judge_bias"  wins, n [, p0]         — an LLM-judge / metric preference vs chance
    kind="backtest"    sr, T, n_trials [, skew, kurt]  — a strategy Sharpe, deflated for #trials
    """
    if kind == "subset_win":
        c = correct_best_of(p["p"], p["n_tests"])
        return {"kind": kind, "survives": bool(c.significant), "raw_p": c.raw_p,
                "corrected_p": c.corrected_p, "method": c.method,
                "verdict": (f"Best-of-{c.n_tested}: raw p={c.raw_p:.3g} -> {c.method} p={c.corrected_p:.3g}; "
                            + ("survives correction." if c.significant
                               else "does NOT survive — within noise for the family tested."))}
    if kind == "model_gap":
        pc = power_check(p["n"], p["p1"], p["p2"])
        return {"kind": kind, "survives": bool(pc.resolvable and pc.significant),
                "gap": pc.diff, "p_value": pc.p_value, "mde": pc.mde,
                "verdict": (f"gap={pc.diff:+.3g} over n={pc.n}; MDE at 80% power={pc.mde:.3g}; "
                            + ("real difference." if pc.resolvable
                               else f"below MDE — indistinguishable at n={pc.n}, not a ranking."))}
    if kind == "judge_bias":
        b = bias_rate(p["wins"], p["n"], p.get("p0", 0.5))
        return {"kind": kind, "survives": not b.biased, "rate": b.rate, "p_value": b.p_value,
                "verdict": (f"{b.wins}/{b.total}={b.rate:.1%} (p={b.p_value:.3g}); "
                            + ("no significant bias vs chance." if not b.biased
                               else "significant bias — the winner may just be longer/first/same-family."))}
    if kind == "backtest":
        v = deflated_sharpe(sr=p["sr"], T=p["T"], n_trials=p.get("n_trials", 1),
                            skew=p.get("skew", 0.0), kurt=p.get("kurt", 3.0))
        return {"kind": kind, "survives": bool(v.survives), "sr": v.sr, "dsr": v.dsr,
                "deflation_bar": v.sr_benchmark, "psr_vs_0": v.psr, "min_track_record": v.min_track_record_length,
                "verdict": str(v)}
    raise ValueError(f"unknown claim kind '{kind}'; use one of {KINDS}")


def _selftest():
    assert verify_claim("subset_win", p=0.009, n_tests=23)["survives"] is False
    assert verify_claim("model_gap", n=2000, p1=0.85, p2=0.80)["survives"] is True
    assert verify_claim("judge_bias", wins=68, n=100)["survives"] is False
    assert verify_claim("backtest", sr=0.12, T=250, n_trials=100)["survives"] is False
    assert verify_claim("backtest", sr=0.15, T=1000, n_trials=1)["survives"] is True
    print("claims selftest: OK (4 claim kinds routed + verified)")


if __name__ == "__main__":
    _selftest()
