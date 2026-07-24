"""hold_the_promise — numguard's accountability oracle: did a backtest's claim survive live trading?

A backtest promises a Sharpe. Months later, the live returns are in. numguard reconciles the promise against
reality. Run:  python examples/hold_the_promise.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from numguard.forward import reconcile, min_periods_to_confirm

rng = random.Random(11)
CLAIMED = 0.15                      # per-period Sharpe the backtest promised (~2.4 annualised)
T = 500                            # live periods observed since the claim


def live(true_sr):
    return [rng.gauss(true_sr * 0.01, 0.01) for _ in range(T)]


scenarios = {
    "kept the promise":   live(0.15),   # reality ~ matches the claim
    "quietly decayed":    live(0.09),   # edge shrank, but is it noise?
    "broke (overfit)":    live(0.00),   # edge gone — the backtest lied
}

print(f"Backtest CLAIMED per-period Sharpe = {CLAIMED} (~{CLAIMED * 252**0.5:.1f} annualised)\n")
for name, rets in scenarios.items():
    r = reconcile(CLAIMED, rets)
    print(f"=== {name} ===")
    print(f"  realized Sharpe : {r['realized_sharpe']}  ({r['edge_survived_pct']}% of the claimed edge)")
    print(f"  verdict         : {r['verdict_label']}   (p={r['p_below']})")
    print(f"  {r['verdict']}\n")

print(min_periods_to_confirm(0.15, 0.09)["note"],
      "— so 'decayed' may just need more live data before you can call it.")
print("\nA Deflated Sharpe verifies the backtest. This verifies REALITY kept the promise.")
