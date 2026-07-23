"""catch_a_fake_backtest — the numguard backtest battery vs three lying strategies.

Shows what a Deflated-Sharpe number alone can't tell you. Run:  python examples/catch_a_fake_backtest.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from numguard.backtest_battery import run_battery, leakage_scan
from numguard.backtest import deflated_sharpe, sharpe

rng = random.Random(7)
N = 750


def ann(sr_per_period):
    return sr_per_period * (252 ** 0.5)


# 1) LOOK-AHEAD: position is the sign of the SAME bar's return. Sharpe looks glorious; it's fiction.
asset = [rng.gauss(0, 0.01) for _ in range(N)]
pos = [1.0 if a > 0 else -1.0 for a in asset]
lookahead = [pos[t] * asset[t] for t in range(N)]

# 2) SMOOTHED / stale marks: an AR(1) return stream — autocorrelation inflates the naive Sharpe.
smoothed, prev = [], 0.0
for _ in range(N):
    prev = 0.6 * prev + rng.gauss(0.0004, 0.01)
    smoothed.append(prev)

# 3) HONEST but weak: real iid edge, modest Sharpe.
honest = [rng.gauss(0.0004, 0.01) for _ in range(N)]

for name, r, extra in [("look-ahead", lookahead, dict(positions=pos, asset_returns=asset)),
                       ("smoothed", smoothed, {}),
                       ("honest-weak", honest, {})]:
    sr = sharpe(r) or 0.0
    dsr = deflated_sharpe(sr=sr, T=N, n_trials=50)
    b = run_battery(r, **extra)
    print(f"\n=== {name} ===")
    print(f"  annualised Sharpe : {ann(sr):+.2f}   (looks {'great' if ann(sr) > 1 else 'ok'})")
    print(f"  Deflated Sharpe   : {dsr.dsr:.2f}  survives={dsr.survives}")
    print(f"  BATTERY risk      : {b['risk'].upper()}  survives={b['survives']}")
    print(f"  flags             : {b['flags'] or 'none'}")
    if "leakage" in b["checks"]:
        lk = b["checks"]["leakage"]
        print(f"  -> leakage_score={lk['leakage_score']} (same-bar corr {lk['corr_same_bar']} vs next-bar {lk['corr_next_bar']})")
    if "hac_sharpe" in b["checks"]:
        hc = b["checks"]["hac_sharpe"]
        print(f"  -> HAC: naive {hc['sharpe']} vs autocorr-adjusted {hc['sharpe_hac']} (inflation {hc['inflation']})")

print("\nThe DSR alone can wave through the look-ahead and smoothed strategies. The battery does not.")
