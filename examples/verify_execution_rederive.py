"""Don't trust a reported Sharpe — re-derive it from the decisions on committed price data.

numguard rebuilds the P&L from positions applied to the asset returns (r_t = pos_{t-1}·assetret_t),
recomputes the Sharpe from that, and checks it matches what was claimed. A number those decisions on that
data don't produce is caught. The result is bound to a hash of the price series.

    python examples/verify_execution_rederive.py

Honest scope (printed): this proves the Sharpe is reconstructible from decisions-on-committed-data — not
that the data is the real market (pass a canonical hash for that) or that the positions weren't overfit.
"""
import random

from numguard import execute as ex, backtest as bt


def main():
    rng = random.Random(11)
    # a real momentum rule on synthetic prices
    asset_returns = [rng.gauss(0.0006, 0.011) for _ in range(400)]
    positions = [0.0] + [(1.0 if asset_returns[t - 1] > 0 else -1.0) for t in range(1, 400)]
    honest_sr = bt.sharpe(ex.reprice(positions, asset_returns)) * (252 ** 0.5)

    print(f"data hash: {ex.data_digest(asset_returns)[:24]}…\n")

    # 1) an operator reporting the honest number → RE-DERIVED, matches
    good = ex.verify_execution(positions, asset_returns, reported_sharpe=honest_sr)
    print(f"reported {honest_sr:+.2f}  ->  re-derived {good['recomputed_sharpe_annualized']:+.2f}  "
          f"->  {good['verdict'].split(' — ')[0]}  (matches={good['matches_reported']})")

    # 2) an operator inflating the Sharpe those same decisions don't produce → MISMATCH, caught
    fake = ex.verify_execution(positions, asset_returns, reported_sharpe=honest_sr + 4.0)
    print(f"reported {honest_sr + 4.0:+.2f}  ->  re-derived {fake['recomputed_sharpe_annualized']:+.2f}  "
          f"->  {fake['verdict'].split(' — ')[0]}  (matches={fake['matches_reported']})")

    print(f"\nattests:        {good['attests']}")
    print(f"does NOT prove: {good['does_not_prove']}")


if __name__ == "__main__":
    main()
