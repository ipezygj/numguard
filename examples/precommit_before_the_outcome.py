"""Pre-register a forward claim BEFORE the outcome, then prove the timeline was never rewritten.

This is numguard's answer to the one thing a self-reported track record can't fake: that the claim came
FIRST, and that no later report was backfilled, reordered, or edited. The receipt/anchor of the
registration digest fixes the claim in time; the hash-chained report log fixes the sequence.

    python examples/precommit_before_the_outcome.py

Honest boundary (printed below): this proves the CLAIM + the TIMELINE. It does NOT prove the reported
return VALUES are real — that needs an operator-uncontrollable feed. numguard says so on every artifact
(data_source: self_reported).
"""
import random
import tempfile
from pathlib import Path

from numguard import precommit as pc, receipt_spec as S, keypair


def main():
    pc._STORE = Path(tempfile.mkdtemp()) / "precommit.db"
    pc._INIT.clear()
    priv, pub = keypair()

    # 1) BEFORE any live data: pre-register the claim. The digest + signature fix "I claim SR=0.2 over the next
    #    250 periods, as of now" in time. Anchor this digest on-chain and it's provable to everyone.
    reg = pc.open_precommitment("momentum_v3", claimed_sr=0.2, horizon_periods=250,
                                private_hex=priv, public_hex=pub, schedule="daily", owner=pc.owner_hash("me"))
    pid = reg["pid"]
    print(f"pre-registered  pid={pid}")
    print(f"  digest={reg['digest'][:24]}…  (anchor this on-chain: {reg['anchor_hint'][:60]}…)")
    print(f"  the registration is itself a verifiable receipt: {S.verify_any(reg['receipt'])['valid']}")

    # 2) Live returns arrive over time. Each report joins a hash-chained, monotonic-time log.
    rng = random.Random(3)
    for week in range(8):
        v = pc.report(pid, [rng.gauss(0.0008, 0.012) for _ in range(5)], owner=pc.owner_hash("me"))
    print(f"\nafter {v['observations']} live observations: {v['verdict_label']}  "
          f"(realized SR {v.get('realized_sharpe', float('nan')):+.3f} vs claimed {v['claimed_sharpe']})")

    # 3) Anyone — no api_key — can audit that the timeline was never rewritten.
    chk = pc.verify_chain(pid)
    print(f"\npublic audit (free): chain ok={chk['ok']}, {chk['reports']} reports, head={chk['head_hash'][:16]}…")

    # 4) Show it's real, not theater: try to backfill a better past week → the chain breaks exactly there.
    c = pc._conn()
    c.execute("UPDATE preport SET returns_digest=? WHERE pid=? AND seq=3", ("ff" * 32, pid)); c.commit(); c.close()
    tampered = pc.verify_chain(pid)
    print(f"after editing report #3: ok={tampered['ok']}  ->  {tampered.get('reason')} (seq {tampered.get('bad_seq')})")

    print("\nHONEST BOUNDARY: this proves the claim predated the outcome and the timeline is intact. It does NOT "
          "prove the reported returns are real (data_source=self_reported) — that needs a trusted feed.")


if __name__ == "__main__":
    main()
