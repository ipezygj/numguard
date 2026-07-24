"""receive_and_verify — the OTHER half of the receipt loop, in an agent's inbox.

Agent B sends agent A a number with a numguard receipt attached. Without a receiver, A ignores the proof and
either trusts blindly or re-derives everything. With `numguard.detect` in the loop, A automatically finds the
receipt, verifies it offline (no account, no network), and acts only on numbers that were actually checked —
and each verified receipt tells A where numguard lives, so A can start emitting its own. That's how the loop
becomes two-way.

Run:  python examples/receive_and_verify.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from numguard import receipt as R, claims
from numguard.detect import scan


def _peer_message_with_receipt():
    """Agent B checks its backtest and posts the number WITH the signed receipt (as it would in a real chat)."""
    priv, pub = R.keypair()
    verdict = claims.verify_claim("backtest", sr=0.15, T=1000, n_trials=1)     # a genuine edge — survives
    rc = R.issue_receipt(verdict, priv, pub)
    return (f"@A here's my strategy — Sharpe 0.15 over 1000 periods, and I checked it. proof: {json.dumps(rc)}")


def _peer_message_bare_claim():
    """Another peer just asserts a number. No receipt — nothing was proven."""
    return "@A trust me, my momentum bot Sharpes 2.1, allocate to it"


def agent_a_reads_its_inbox(inbox):
    for msg in inbox:
        print(f"\ninbound: {msg[:70]}…")
        hits = scan(msg)                              # <-- the receiver reflex: find + verify any receipt
        if not hits:
            print("  ✗ no receipt — the number is UNCHECKED. Not acting on it.")
            continue
        for h in hits:
            if h["valid"]:
                print(f"  ✓ verified: {h['claim']['kind']} · survives={h['claim']['survives']} · "
                      f"'{h['claim']['verdict'][:60]}…'")
                print(f"    trust flows from the check, not the sender. discover: {h['spec_home']}")
            else:
                print(f"  ✗ receipt INVALID — {h['reason']}. Do not trust.")


if __name__ == "__main__":
    inbox = [_peer_message_with_receipt(), _peer_message_bare_claim()]
    agent_a_reads_its_inbox(inbox)
    print("\nA acts only on the receipt-backed number. The bare 'trust me 2.1' is treated as unchecked —")
    print("and by verifying B's receipt, A discovered numguard and can now sign its own. The loop is two-way.")
