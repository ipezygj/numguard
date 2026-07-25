"""execute — verifiable backtest: RE-DERIVE the number from decisions on committed data, don't trust it reported.

The data-trust root problem: every statistical check assumes the returns series it's handed is real. A fraudster
just reports a fabricated series (or a Sharpe that doesn't match its own returns). This module removes that
assumption in the cheapest way a solo dev can ship — no arbitrary-code execution (no RCE), no TEE:

  numguard RECONSTRUCTS the strategy's P&L from the operator's own POSITIONS applied to a hash-committed MARKET
  DATA series (return_t = position_{t-1} * asset_return_t - costs), recomputes the Sharpe from THAT, and checks
  it matches what was reported. A reported number that can't be rebuilt from decisions-on-real-prices is caught.

What this EARNS (and the receipt says exactly this, no more):
  - the reported Sharpe is RECONSTRUCTIBLE from the stated positions applied to the committed price series
    (catches a Sharpe that doesn't match its own returns, and returns fabricated independently of the prices);
  - the result is BOUND to a specific market-data hash (so "which data?" is pinned, not hand-wavy).

What it does NOT prove (kept explicit — no unearned evidence):
  - that the committed data IS the real market (only true if the hash is checked against a canonical public
    source — pass `canonical_hash` to assert that);
  - that the positions weren't OVERFIT in-sample (that's what Deflated Sharpe / PBO catch — complementary);
  - it does not run the strategy's code (the full TEE/sandbox re-execution is the heavier next tier).
So: "re-derived M from these decisions on data H", not "this strategy truly makes money".
"""
from __future__ import annotations
import hashlib
import hmac
import json

from . import backtest as _bt


def data_digest(asset_returns) -> str:
    """Canonical hash of the market-data series the result is bound to."""
    return hashlib.sha256(json.dumps([round(float(x), 12) for x in asset_returns],
                                     separators=(",", ":")).encode()).hexdigest()


def reprice(positions, asset_returns, cost_bps: float = 0.0):
    """Reconstruct per-period strategy returns from positions on the asset: r_t = pos_{t-1} * assetret_t, minus
    turnover cost. positions and asset_returns are aligned; the position at t-1 earns the asset move into t."""
    n = min(len(positions), len(asset_returns))
    if n < 2:
        raise ValueError("need >= 2 aligned periods of positions and asset_returns")
    cost = cost_bps / 10000.0
    out = []
    for t in range(1, n):
        held = float(positions[t - 1])
        pnl = held * float(asset_returns[t])
        turnover = abs(float(positions[t]) - held) if t < len(positions) else 0.0
        out.append(pnl - turnover * cost)
    return out


def verify_execution(positions, asset_returns, reported_sharpe=None, cost_bps: float = 0.0,
                     periods_per_year: int = 252, n_trials: int = 1, canonical_hash: str = "",
                     tol: float = 0.02) -> dict:
    """Re-derive the strategy's Sharpe from positions applied to committed asset returns, and check it matches the
    reported one. Returns the recomputed metric, whether it matches, the data hash it's bound to, and a Deflated
    Sharpe on the re-derived series so the two checks compose."""
    if not isinstance(positions, (list, tuple)) or not isinstance(asset_returns, (list, tuple)):
        raise ValueError("positions and asset_returns must be lists")
    strat = reprice(positions, asset_returns, cost_bps=cost_bps)
    per_bar = _bt.sharpe(strat)
    ann = per_bar * (periods_per_year ** 0.5)
    dh = data_digest(asset_returns)

    out = {
        "kind": "verified_execution",
        "recomputed_sharpe_per_bar": round(per_bar, 6),
        "recomputed_sharpe_annualized": round(ann, 6),
        "observations": len(strat),
        "data_hash": dh,
        "cost_bps": cost_bps,
        # honest scope: derived from decisions on committed data, NOT proof the data is real or the fit is OOS
        "attests": "reported Sharpe is reconstructible from these positions on the committed price series",
        "does_not_prove": "that the committed data is the real market, or that the positions weren't overfit",
    }

    if canonical_hash:
        out["data_matches_canonical"] = hmac.compare_digest(dh, str(canonical_hash))
        if not out["data_matches_canonical"]:
            out["survives"] = False
            out["verdict"] = "REJECTED — committed data does not match the canonical source hash provided."
            return out

    if reported_sharpe is not None:
        rep = float(reported_sharpe)
        # match on whichever scale is closer (accept the operator reporting per-bar or annualized)
        gap = min(abs(rep - per_bar), abs(rep - ann))
        scale = max(1e-9, abs(rep))
        out["reported_sharpe"] = rep
        out["matches_reported"] = bool(gap <= max(tol, tol * scale))
        out["survives"] = out["matches_reported"]
        out["verdict"] = ("RE-DERIVED — the reported Sharpe matches the number rebuilt from positions on the "
                          "committed data." if out["matches_reported"] else
                          "MISMATCH — the reported Sharpe cannot be reconstructed from these positions on this "
                          "data; the number is not what these decisions produce.")
    else:
        out["survives"] = None
        out["verdict"] = "RE-DERIVED — no reported Sharpe given to compare; recomputed value returned."

    # compose with the overfitting check on the re-derived series
    dsr = _bt.deflated_sharpe(sr=per_bar, T=len(strat), n_trials=int(n_trials))
    out["deflated_sharpe"] = round(dsr.dsr, 4)
    out["deflated_survives"] = dsr.survives
    return out


def _selftest():
    import random
    rng = random.Random(0)
    # a real momentum rule on synthetic prices: position = sign of last return
    aret = [rng.gauss(0.0005, 0.01) for _ in range(400)]
    pos = [0.0] + [(1.0 if aret[t - 1] > 0 else -1.0) for t in range(1, 400)]
    strat = reprice(pos, aret)
    true_sr = _bt.sharpe(strat) * (252 ** 0.5)

    ok = verify_execution(pos, aret, reported_sharpe=true_sr)
    assert ok["matches_reported"] and ok["survives"], ok
    # a fabricated Sharpe that these decisions do NOT produce -> caught
    bad = verify_execution(pos, aret, reported_sharpe=true_sr + 3.0)
    assert bad["matches_reported"] is False and bad["survives"] is False, bad
    # data binding: wrong canonical hash -> rejected before anything else
    rej = verify_execution(pos, aret, reported_sharpe=true_sr, canonical_hash="00" * 32)
    assert rej["survives"] is False and "canonical" in rej["verdict"]
    # data hash is stable + changes if the data changes
    assert data_digest(aret) == ok["data_hash"] and data_digest(aret[:-1]) != ok["data_hash"]
    print(f"execute selftest: OK (re-derived SR {true_sr:+.3f} matched; fabricated SR caught; "
          f"data-hash bound; canonical-mismatch rejected)")


if __name__ == "__main__":
    _selftest()
