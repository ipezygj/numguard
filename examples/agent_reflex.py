"""agent_reflex — verification as a reflex in an agent's loop, in one line.

An agent evaluates strategies and would allocate to the best backtest. Without a gate it trusts a look-ahead
fraud. With `numguard.guard.require_backtest` in the loop, it physically cannot.

Run:  python examples/agent_reflex.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from numguard.guard import require_backtest, NumberNotVerified

rng = random.Random(7)
N = 750
asset = [rng.gauss(0, 0.01) for _ in range(N)]

candidates = {
    # a look-ahead fraud: position = sign of the SAME bar's return. Gorgeous Sharpe, pure fiction.
    "momentum-v3": {"returns": [(1 if asset[t] > 0 else -1) * asset[t] for t in range(N)],
                    "positions": [1.0 if asset[t] > 0 else -1.0 for t in range(N)], "asset_returns": asset},
    # an honest, genuine iid edge (real signal, no time-travel) — this one should pass
    "carry-v1": {"returns": [rng.gauss(0.002, 0.008) for _ in range(1000)]},
}


def agent_picks_a_strategy():
    # policy: never allocate to a CRITICAL/HIGH backtest (fraud or fragile); a soft flag is allowed with a note.
    for name, data in candidates.items():
        print(f"\nagent evaluates '{name}'…")
        try:
            v = require_backtest(**data)                 # <-- the reflex: one line, raises unless the battery is clean
            print(f"  ✓ clean. Allocate to '{name}'.")
            return name
        except NumberNotVerified as e:
            risk, flags = e.verdict["risk"], e.verdict["flags"]
            if risk in ("critical", "high"):
                print(f"  ✗ REJECTED — {risk.upper()}: {flags}. Not allocatable.")
            else:
                print(f"  ⚠ allocatable with caution — {risk.upper()}: {flags} (a soft flag, not fraud).")
                return name
    return None


picked = agent_picks_a_strategy()
print(f"\nagent allocates to: {picked or 'nothing'}")
print("Without the gate it would have trusted 'momentum-v3' — a Sharpe of 20 built from time it never had.")
