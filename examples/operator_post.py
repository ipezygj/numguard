"""operator_post — the quant-on-X wedge: publish a backtest number that carries its own proof.

For an operator who posts trading/backtest claims to an audience. Instead of "trust me, Sharpe 0.15", the
number goes out WITH a signed receipt any follower (or their agent) can verify offline. In a space full of
backtest grift, a verifiable claim is the differentiator — and it costs nothing: local, no account, no gas.

    python examples/operator_post.py

Wire this into an outbound hook (a bot's post step, a Substack draft, a webhook) so every numeric claim is
checked and stamped before it ships. If the number doesn't survive, it won't go out stamped 'verified'.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from numguard.emit import announce, operator_keypair, NotVerified

priv, pub = operator_keypair()          # your stable identity — generated + persisted once, reused every post
print(f"operator public key: {pub or '(hmac fallback)'}\n")

# 1) A real, honest edge — ships with a verifiable receipt embedded.
post = announce("backtest", priv, pub,
                "Weekly update: the carry book Sharped 0.15 over 1000 sessions. Checked, not vibes.",
                sr=0.15, T=1000, n_trials=1)
print("READY TO POST:\n" + post + "\n")
print("-" * 70)

# 2) An overfit number the operator was tempted to post — the reflex stops it.
try:
    announce("backtest", priv, pub,
             "New alpha! Sharpe 0.12, looks amazing",
             sr=0.12, T=250, n_trials=100)
except NotVerified as e:
    print("\nBLOCKED before posting — this one was the best of 100 configs:")
    print("  " + e.verdict["verdict"])
    print("  numguard won't let you stamp it 'verified'. Post the honest version, or don't post it.")
